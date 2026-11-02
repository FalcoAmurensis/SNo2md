"""Experimental whole-document chunking; does not change note export layout.

The native payload is retained separately from the reader-facing Markdown.
Images added from figure/table source locations are explicitly labelled.
"""
import base64
from dataclasses import fields, is_dataclass

from .markdown_engine import load_engine


SETTINGS = {
    'default': {'max_tokens': 400, 'min_tokens': 120, 'table_mode': 'preserve'},
    'small': {'max_tokens': 200, 'min_tokens': 60, 'table_mode': 'preserve'},
    'large': {'max_tokens': 800, 'min_tokens': 240, 'table_mode': 'preserve'},
    'isolated_tables': {'max_tokens': 400, 'min_tokens': 120, 'table_mode': 'isolate'},
}


def extract_chunks(document, setting='default'):
    """Pass the entire open PDF to the native API, with OCR disabled."""
    load_engine()
    return document.to_chunks(**SETTINGS[setting], respect_section_starts=True,
                              header_footer_mode='exclude', use_ocr=False,
                              force_ocr=False, embed_images=True, force_text=True,
                              show_progress=False)


def json_value(value):
    """Serialize public structure, excluding private runtime references."""
    if isinstance(value, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)
                if not f.name.startswith('_')}
    if isinstance(value, dict) or hasattr(value, 'items'):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    return value


def structure_payload(chunked):
    return {'chunks': chunked.to_dicts(), 'elements': json_value(chunked.elements),
            'tables': json_value(chunked.tables), 'figures': json_value(chunked.figures),
            'sections': json_value(chunked.sections), 'hierarchy': json_value(chunked.hierarchy),
            'params': json_value(chunked.params), 'diagnostics': json_value(chunked.diagnostics)}


def image_markdown(data, label):
    return f'![{label}](data:image/png;base64,{base64.b64encode(data).decode("ascii")})'


def crop_image(document, page, bbox):
    pdf, _ = load_engine()
    clip = pdf.Rect(bbox) & document[page - 1].rect
    if clip.is_empty or clip.is_infinite:
        raise ValueError(f'Invalid source rectangle: page {page}, {bbox}')
    return document[page - 1].get_pixmap(clip=clip, dpi=150, alpha=False).tobytes('png')


def render_chunked_markdown(chunked, document, title, source_link):
    """Return readable chunks and a log of supplements, without changing native text."""
    params = getattr(chunked, 'params', SETTINGS['default'])
    rows = [f'# {title} · 청킹\n',
            f'설정: {params["max_tokens"]}/{params["min_tokens"]} 추정 토큰, '
            f'{params["table_mode"]}. 토큰 수는 문자 기반 추정치입니다.\n',
            '본문은 네이티브 청크 순서입니다. 객체 이미지 보완은 별도 표시합니다.\n']
    supplements = []
    for number, chunk in enumerate(chunked, 1):
        meta = chunk.metadata
        section = ' > '.join(meta.section_path) or '(섹션 미검출)'
        body = chunk.text
        for table_id in meta.table_ids:
            table = chunked.get(table_id)
            canonical = table.text.strip()
            if canonical and canonical in body:
                # Adjacent native tables can otherwise parse as one GFM table.
                # Preserve their text, inserting only presentation boundaries.
                body = body.replace(canonical,
                    f'\n\n<!-- pmp2:table {table_id} -->\n\n{canonical}\n\n', 1)
                supplements.append({'chunk_id': chunk.id, 'object_id': table_id,
                    'page': table.page, 'bbox': table.bbox,
                    'reason': 'presentation table boundary; native text retained in JSON'})
        rows += [f'## 청크 {number} · {chunk.id}\n',
                 f'섹션: {section}  \n원본: [{meta.page_start}–{meta.page_end}쪽]'
                 f'({source_link}#page={meta.page_start})  \n추정 토큰: {meta.token_count}\n',
                 body]
        for figure_id in meta.figure_ids:
            figure = chunked.get(figure_id)
            data = figure.image or crop_image(document, figure.page, figure.bbox)
            uri = base64.b64encode(data).decode('ascii')
            # Native text may already embed the exact bytes. Avoid adding them twice.
            if uri in chunk.text:
                continue
            rows += [f'\n객체 이미지 보완: {figure_id} ({figure.boxclass}), 원본 {figure.page}쪽\n',
                     image_markdown(data, f'{figure_id}, page {figure.page}')]
            supplements.append({'chunk_id': chunk.id, 'object_id': figure_id,
                                'page': figure.page, 'bbox': figure.bbox,
                                'reason': 'native chunk has no embedded figure image'})
        for table_id in meta.table_ids:
            table = chunked.get(table_id)
            if not table.text.strip():
                rows += [f'\n빈 표 이미지 보완: {table_id}\n', image_markdown(
                    crop_image(document, table.page, table.bbox), table_id)]
                supplements.append({'chunk_id': chunk.id, 'object_id': table_id,
                                    'page': table.page, 'bbox': table.bbox, 'reason': 'empty table'})
        rows.append('\n---\n')
    return '\n\n'.join(rows), supplements


def validate_chunks(chunked, page_count):
    """Report evidence consistency, leaving semantic judgement to visual review."""
    elements = {e.id: e for e in chunked.elements}
    errors, references = [], set()
    for index, chunk in enumerate(chunked):
        meta = chunk.metadata
        if chunk.id != f'c{index}':
            errors.append(f'nonsequential chunk id: {chunk.id}')
        if not 1 <= meta.page_start <= meta.page_end <= page_count:
            errors.append(f'invalid pages: {chunk.id}')
        for key in meta.element_ids:
            if key not in elements:
                errors.append(f'unknown element: {key}')
            else:
                references.add(key)
        for key in meta.table_ids + meta.figure_ids:
            obj = chunked.get(key, default=None)
            if obj is None or obj.chunk_id != chunk.id:
                errors.append(f'object owner mismatch: {chunk.id}/{key}')
    return {'errors': errors, 'unreferenced_body_elements': [e.id for e in chunked.elements
            if e.text.strip() and not e.is_header_footer and e.id not in references],
            'excluded_header_footer_elements': [e.id for e in chunked.elements if e.is_header_footer],
            'duplicate_nonempty_chunk_hashes': len([c for c in chunked if c.text.strip()]) -
                len({c.content_hash for c in chunked if c.text.strip()}),
            'cross_page_chunks': sum(c.metadata.page_start != c.metadata.page_end for c in chunked),
            'oversize_chunks': [c.id for c in chunked if c.metadata.token_count > chunked.params['max_tokens']]}
