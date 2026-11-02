"""Native PDF chunks and published user memos, outside the user's Vault."""
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .locations import state_directory
from .markdown_regions import _layout,split,digest

SCHEMA=1
PAGE_METADATA_SCHEMA=1
SETTINGS=dict(max_tokens=400,min_tokens=120,table_mode='preserve',respect_section_starts=True,
              header_footer_mode='exclude',use_ocr=False,force_ocr=False,embed_images=False,
              force_text=True,show_progress=False)


def _encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode('utf-8')
def _read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle,name=tempfile.mkstemp(prefix='.chunk-',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(handle,'wb') as stream:
            stream.write(_encoded(value));stream.flush();os.fsync(stream.fileno())
        for attempt in range(5):
            try:os.replace(temporary,path);break
            except PermissionError:
                if attempt==4:raise
                time.sleep(.03)
    finally:
        temporary.unlink(missing_ok=True)


def note_id(identity):return digest(str(identity).encode('utf-8'))


def memo_parts(text,target=1600):
    """Group paragraphs while keeping fenced code and tables intact.

    Target uses an explicit character/4 token estimate. Long indivisible blocks
    may exceed the target, just as preserved native tables may exceed max_tokens.
    """
    blocks=[];start=position=0;fence=None
    for line in text.splitlines(keepends=True):
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if marker:
            if fence is None:fence=marker[1]
            elif marker[1][0]==fence[0] and len(marker[1])>=len(fence) and not line[marker.end():].strip():fence=None
        position+=len(line)
        if not line.strip() and fence is None:
            blocks.append(text[start:position]);start=position
    if start<len(text):blocks.append(text[start:])
    pending=''
    for block in blocks:
        if pending and len(pending)+len(block)>target:
            yield pending;pending=''
        pending+=block
    if pending:yield pending


class ChunkCache:
    def __init__(self,root=None,*,identity=None,source_revision=None,origin=None,defer=False):
        self.root=Path(root) if root is not None else state_directory()/'chunks'
        self.identity=identity;self.source_revision=source_revision;self.origin=origin;self.defer=defer
        self.engine=version('pymupdf')
        self.engine_key=digest(_encoded(dict(schema=SCHEMA,engine=self.engine,settings=SETTINGS)))

    def index_path(self,identity=None):
        key=identity or self.identity
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('invalid chunk note identity')
        return self.root/'notes'/(key+'.json')

    def _source(self,note,asset,engine):
        binary=note.files[asset];source_hash=digest(binary)
        key=digest(_encoded([source_hash,self.engine_key]));relative='documents/'+key+'.json';path=self.root/relative
        try:
            cached=_read(path)
            if (cached['schema']==SCHEMA and cached['source_sha256']==source_hash and
                cached['engine_key']==self.engine_key and cached['payload_sha256']==digest(_encoded(cached['payload']))):
                return dict(asset=asset,source_sha256=source_hash,cache_file=relative,chunk_count=len(cached['payload']['chunks']),cache_hit=True)
        except (OSError,ValueError,KeyError,TypeError):pass
        from .markdown_chunks import structure_payload
        document=engine.documents.get(asset)
        if document is None:
            document=engine.pdf.open(stream=binary,filetype='pdf');engine.documents[asset]=document
        payload=structure_payload(document.to_chunks(**SETTINGS))
        atomic_json(path,dict(schema=SCHEMA,kind='source_pdf',source_sha256=source_hash,
            engine_key=self.engine_key,engine_version=self.engine,settings=SETTINGS,
            payload_sha256=digest(_encoded(payload)),payload=payload))
        return dict(asset=asset,source_sha256=source_hash,cache_file=relative,chunk_count=len(payload['chunks']),cache_hit=False)

    def prepare(self,note,engine,source,vault,backgrounds=None):
        root=self.root.resolve()
        for p in [Path(source).resolve(),Path(vault).resolve()]:
            if root==p or root.is_relative_to(p) or p.is_relative_to(root):
                raise ValueError('chunk cache must be separate from source and Vault')
        from .export import page_metadata
        from .pdf import prepare_backgrounds
        if backgrounds is None:backgrounds=prepare_backgrounds(note)
        if len(backgrounds)!=len(note.pages):raise ValueError('page metadata/background count mismatch')
        identity=self.identity or note_id(os.path.normcase(str(Path(source).resolve())))
        pages=[];assets=[]
        for i,p in enumerate(note.pages,1):
            refs=[]
            for bg in p.get('properties',{}).get('pdf',[]):
                asset=bg['asset'];refs.append(dict(asset=asset,source_page=bg['page_index']+1))
                if asset not in assets:assets.append(asset)
            key=str(p.get('source_page_id') or p.get('id') or f'page-{i}')
            pages.append(dict(note_page=i,page_key=digest(key.encode('utf-8')),source_refs=refs,
                              metadata=page_metadata(i,p,backgrounds[i-1])))
        revision=self.source_revision or digest(_encoded([[k,digest(v)] for k,v in sorted(note.files.items())]))
        return dict(schema=SCHEMA,kind='note_chunk_index',note_id=identity,source_revision=revision,
            engine_key=self.engine_key,title=note.title,origin=str(self.origin or Path(source).resolve()),
            page_metadata_schema=PAGE_METADATA_SCHEMA,pages=pages,documents=[self._source(note,a,engine) for a in assets])

    def commit(self,manifest,markdown,pdf,expected_managed=None):
        markdown,pdf=Path(markdown),Path(pdf)
        before=markdown.stat();data=markdown.read_bytes();stat=markdown.stat()
        if (before.st_size,before.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):
            raise ValueError('Markdown changed while reading; chunk index retained')
        managed,tail=split(data);managed_hash=digest(managed)
        if expected_managed and managed_hash!=expected_managed:
            raise ValueError('generated Markdown changed; chunk index retained')
        _,slots=_layout(data);bykey={p['page_key']:p for p in manifest['pages']};chunks=[]
        raw_memos=[]
        regions=[]
        for key,start,content,stop,end in slots:
            page=bykey.get(key.decode());scope='page' if page else 'removed_page'
            regions.append((scope,key.decode(),page['note_page'] if page else None,data[content:stop]))
        regions.append(('document','document',None,tail))
        for scope,key,page,raw in regions:
            raw_memos.append([scope,key,digest(raw)])
            text=raw.decode('utf-8')
            text=re.sub(r'^\s*#{2,3}[ \t]+사용자 메모[ \t]*(?:\r?\n|$)','',text,count=1)
            if not text.strip():continue
            for i,part in enumerate(memo_parts(text)):
                if not part.strip():continue
                chunks.append(dict(id=f'memo:{scope}:{key}:{i}',kind='user_memo',scope=scope,
                    page_key=key,note_page=page,text=part,estimated_tokens=math.ceil(len(part)/4),
                    token_estimate_method='characters_div4',oversized=len(part)>1600))
        index=dict(manifest,user_chunks=chunks,markdown=str(markdown.resolve()),pdf=str(pdf.resolve()),output_name=markdown.stem,
            managed_sha256=managed_hash,memo_sha256=digest(_encoded(raw_memos)),
            markdown_stat=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns))
        path=self.index_path(manifest['note_id']);atomic_json(path,index);return path

    def refresh_memos(self,identity,markdown,pdf,source_revision,expected_managed):
        """Return False when native data must be rebuilt; never modify PDF/MD."""
        try:index=_read(self.index_path(identity))
        except (OSError,ValueError):return False
        if index.get('schema')!=SCHEMA or index.get('engine_key')!=self.engine_key or index.get('source_revision')!=source_revision:return False
        if index.get('page_metadata_schema')!=PAGE_METADATA_SCHEMA or any('metadata' not in p for p in index.get('pages',[])):return False
        for document in index['documents']:
            path=(self.root/document['cache_file']).resolve()
            if not path.is_relative_to((self.root/'documents').resolve()) or not path.is_file():return False
        stat=Path(markdown).stat();stamp=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        if index.get('markdown_stat')!=stamp or index.get('markdown')!=str(Path(markdown).resolve()):
            self.commit(index,markdown,pdf,expected_managed)
        return True
