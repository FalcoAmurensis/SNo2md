"""Public conversion operations. Single exports never overwrite existing files."""
import os
from pathlib import Path
import tempfile
import time

from .decoder import read_note
from .export import render_markdown
from .markdown_engine import SourceMarkdown
from .model import ConversionOptions, ConversionResult, OutputError
from .image_assets import asset_folder, externalize_images
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


def export_note(source, vault_root, *, name=None, options=None, timings=None, chunk_cache=None,
                image_review=None, layout_debug=False):
    """Decode once and export a portable PDF/MD/images document folder by default.

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
    output = new_path(vault/name) if options.document_folders else vault
    pdf_path, md_path = new_path(output/(name+'.pdf')), new_path(output/(name+'.md'))
    folder = asset_folder(name, options.document_folders)
    if options.image_storage == 'external':
        new_path(output/folder)
    for path in (pdf_path, md_path): protect_source(source,path)
    note = measured("decode", read_note, source)
    backgrounds = measured("backgrounds", prepare_backgrounds, note)
    published = []
    created_directories = []
    image_assets = {}
    chunk_manifest = None
    chunk_warnings = []
    cache = None
    review_report = None
    try:
        vault.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.sno2md-', dir=vault) as output_tmp:
            output_stage = Path(output_tmp)
            counts = measured('pdf', write_pdf, note, output_stage/'output.pdf', options, backgrounds)
            started = time.perf_counter()
            layout_options = {}
            if options.markdown_mode != 'classic' or layout_debug:
                layout_options = dict(markdown_mode=options.markdown_mode, layout_debug=layout_debug)
            with SourceMarkdown(note, **layout_options) as engine:
                if options.review_images:
                    try:
                        if image_review is None:
                            from .image_review import ImageReview
                            from .locations import state_directory
                            from .chunk_cache import note_id
                            review_root = Path(chunk_cache.root).parent/'image-review' if chunk_cache is not None else state_directory()/'image-review'
                            identity = note_id(str(source.resolve())+'\0'+str(vault))
                            image_review = ImageReview(review_root, identity, distance=options.repeat_distance, source=source)
                        from .locations import require_separate
                        require_separate(vault,image_review.store.root)
                        review_report = measured('image_review', image_review.prepare, note, backgrounds, engine)
                    except Exception as error:
                        chunk_warnings.append(f'Image review unavailable; images retained: {error}')
                markdown = render_markdown(note, pdf_path.name, backgrounds, engine)
                layout_report = engine.layout_report() if layout_debug else None
                deferred = [dict(record, asset=asset, source_page=page+1)
                            for (asset, page), records in engine.object_reports.items()
                            for record in records if record['action'] == 'unresolved']
                try:
                    from .chunk_cache import ChunkCache
                    cache = chunk_cache if chunk_cache is not None else ChunkCache()
                    chunk_manifest = measured('chunks', cache.prepare, note, engine, source, vault, backgrounds)
                except Exception as error:
                    chunk_warnings.append(f'Chunk cache/page metadata unavailable; PDF/Markdown retained: {error}')
            timings['markdown'] = time.perf_counter()-started
            if options.image_storage == 'external':
                markdown, image_assets = externalize_images(markdown, output_stage, folder=folder)
            (output_stage/'output.md').write_text(markdown, encoding='utf-8')
            if options.document_folders:
                output.mkdir()
                created_directories.append(output)
            if image_assets:
                (output/folder).mkdir()
                created_directories.append(output/folder)
                for relative in image_assets:
                    target = output/relative
                    publish_file(output_stage/relative, target)
                    published.append((target, target.stat()))
            for staged, target in ((output_stage/'output.pdf',pdf_path),(output_stage/'output.md',md_path)):
                publish_file(staged,target)
                published.append((target,target.stat()))
        warnings = ['Markdown uses original PDF pages without OCR; reading order, tables, and formulas may need review.']
        warnings.extend(f"Markdown object retained: {r['asset']} page {r['source_page']} "
                        f"{r['key']}: {r['reason']}" for r in deferred)
        chunk_index = None
        if chunk_manifest is not None:
            try:
                if cache.defer:
                    from .chunk_cache import atomic_json
                    chunk_index = output/'chunk-manifest.json'
                    atomic_json(chunk_index, chunk_manifest)
                else:
                    chunk_index = cache.commit(chunk_manifest, md_path, pdf_path)
            except Exception as error:
                chunk_warnings.append(f'Chunk index unavailable; PDF/Markdown retained: {error}')
        warnings.extend(chunk_warnings)
        if review_report is not None and not image_review.defer:
            try:
                image_review.store.commit(review_report, md_path, pdf_path)
            except Exception as error:
                warnings.append(f'Image review index unavailable: {error}')
        return ConversionResult(pdf=pdf_path, markdown=md_path, counts=counts, warnings=warnings,
                                chunk_index=chunk_index, image_assets=image_assets, image_review_report=review_report,
                                layout_report=layout_report)
    except Exception as error:
        failures = []
        for path, stamp in reversed(published):
            try:
                if path.exists() and path.stat().st_ino == stamp.st_ino and path.stat().st_mtime_ns == stamp.st_mtime_ns:
                    path.unlink()
                elif path.exists(): failures.append(f'changed file retained: {path}')
            except OSError as cleanup: failures.append(str(cleanup))
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                if directory.exists(): failures.append(f'nonempty directory retained: {directory}')
        if failures:
            raise OutputError(f'{error}; cleanup incomplete: {failures}') from error
        if isinstance(error,OSError): raise OutputError(f'bundle output failed: {error}') from error
        raise
