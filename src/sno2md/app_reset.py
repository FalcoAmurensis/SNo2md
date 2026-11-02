"""Disconnect synchronization without deleting source notes or Vault files."""
from pathlib import Path
import shutil

from .markdown_regions import split, digest
from .sync_storage import Conflict, file_digest, output_paths, recover, save_json


def finish_reset(root):
    """Caller holds store_lock; finish clearing app-owned synchronization data."""
    root=Path(root);pending=root/'reset-pending.json'
    if not pending.exists():return
    base=root.resolve()
    if base==Path(base.anchor):raise Conflict('app state cannot be a filesystem root')
    names=['config.json','state.json','pairs','chunks','resets','reset-outputs.json','reset-ready.json']
    names.extend(p.name for p in root.glob('job-*'))
    for name in names:
        source=root/name
        if not source.resolve().is_relative_to(base) or source.is_symlink() or getattr(source,'is_junction',lambda:False)():
            raise Conflict('reset target escapes app state')
        if source.exists():
            if source.is_dir():shutil.rmtree(source)
            else:source.unlink()
    # This flag contains no previous paths, hashes or note identities.
    save_json(root/'reset-ready.json',dict(recreate_outputs=True))
    pending.unlink()


def disconnect(root):
    """Caller holds store_lock. Clear all file histories and connections."""
    from .sync_config import load_config, load_state
    root=Path(root);finish_reset(root)
    config=load_config(root);state=load_state(root);recover(root,config,state)
    save_json(root/'reset-pending.json',dict(schema=2))
    finish_reset(root)


def adopt_output(root,config,entry):
    """Snapshot occupied targets for a fresh regeneration, without old history."""
    if not config.get('recreate_outputs'):return
    paths=output_paths(config,entry)
    hashes={ext:file_digest(path) for ext,path in paths.items()}
    if not any(hashes.values()):return
    baseline=dict(outputs=hashes,input=None,options=None,generator_version='reset',revision=0)
    if hashes['md']:
        data=paths['md'].read_bytes()
        if digest(data)!=hashes['md']:raise Conflict('Markdown changed while starting fresh synchronization')
        try:baseline['md_managed_hash']=digest(split(data)[0])
        except ValueError:entry['reset_preserve_markdown']=True
    entry['success']=baseline
