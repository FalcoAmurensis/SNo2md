"""Public conversion operations. Single exports never overwrite existing files."""
import os
from pathlib import Path
import tempfile
import time

from .decoder import read_note
from .export import render_markdown
from .markdown_engine import SourceMarkdown
from .model import ConversionOptions, ConversionResult, OutputError
from .pdf import prepare_backgrounds, write_pdf


def new_path(value):
    path = Path(value).absolute()
    if os.path.lexists(path): raise FileExistsError(f'output already exists: {path}')
    return path


def protect_source(source, destination):
    if source.is_dir() and destination.resolve().is_relative_to(source.resolve()):
        raise ValueError('output must be outside the source note folder')


def publish_file(source, destination):
    # Fully write first. A hard link publishes without ever replacing another file.
    with open(source,'r+b') as stream: os.fsync(stream.fileno())
    os.link(source, destination)


def convert_pdf(source, destination, *, options=None):
    options = options or ConversionOptions()
    source, destination = Path(source), new_path(destination)
    protect_source(source, destination)
    note = read_note(source)
    backgrounds = prepare_backgrounds(note)
    try:
        destination.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.sno2md-', dir=destination.parent) as temporary:
            staged = Path(temporary)/'output.pdf'
            counts = write_pdf(note, staged, options, backgrounds)
            publish_file(staged, destination)
        return ConversionResult(pdf=destination, counts=counts)
    except OSError as error:
        raise OutputError(f'PDF output failed: {error}') from error


def export_note(source, vault_root, *, name=None, options=None, timings=None):
    """Decode once in memory and write PDF/MD without an external asset store.

    Existing outputs are never replaced here. The sync layer manages replacement/recovery.
    """
    timings = timings if timings is not None else {}
    def measured(label, function, *args):
        started = time.perf_counter()
        try:
            return function(*args)
        finally:
            timings[label] = time.perf_counter()-started
    options = options or ConversionOptions()
    source, vault = Path(source), Path(vault_root).resolve()
    name = name if name is not None else (source.stem if source.is_file() else source.name)
    if (not name or name in ('.','..') or any(c in name for c in '/\\:*?"<>|')
            or any(ord(c)<32 for c in name) or name.endswith((' ','.'))):
        raise ValueError('name must be a valid filename stem, without directories')
    pdf_path, md_path = new_path(vault/(name+'.pdf')), new_path(vault/(name+'.md'))
    for path in (pdf_path, md_path): protect_source(source,path)
    note = measured("decode", read_note, source)
    backgrounds = measured("backgrounds", prepare_backgrounds, note)
    published = []
    try:
        vault.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.sno2md-', dir=vault) as output_tmp:
            output_stage = Path(output_tmp)
            counts = measured('pdf', write_pdf, note, output_stage/'output.pdf', options, backgrounds)
            started = time.perf_counter()
            with SourceMarkdown(note) as engine:
                markdown = render_markdown(note, pdf_path.name, backgrounds, engine)
            timings['markdown'] = time.perf_counter()-started
            (output_stage/'output.md').write_text(markdown, encoding='utf-8')
            for staged, target in ((output_stage/'output.pdf',pdf_path),(output_stage/'output.md',md_path)):
                publish_file(staged,target)
                published.append((target,target.stat()))
        return ConversionResult(pdf=pdf_path, markdown=md_path, counts=counts,
                                warnings=['Markdown uses original PDF pages without OCR; reading order, tables, and formulas may need review.'])
    except Exception as error:
        failures = []
        for path, stamp in reversed(published):
            try:
                if path.exists() and path.stat().st_ino == stamp.st_ino and path.stat().st_mtime_ns == stamp.st_mtime_ns:
                    path.unlink()
                elif path.exists(): failures.append(f'changed file retained: {path}')
            except OSError as cleanup: failures.append(str(cleanup))
        if failures:
            raise OutputError(f'{error}; cleanup incomplete: {failures}') from error
        if isinstance(error,OSError): raise OutputError(f'bundle output failed: {error}') from error
        raise
