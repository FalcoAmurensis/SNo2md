"""Assemble original-PDF Markdown and visible stroke metadata in note page order."""
import json
import re
from urllib.parse import quote
from .handwriting import page_handwriting
from .model import MARKDOWN_SCHEMA, GENERATOR_VERSION, RenderError


def nested_headings(text):
    """Keep extracted headings below the source section; preserve fenced code."""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
        elif fence is None:
            heading = re.match(r'^(#{1,6})[ \t]+', line)
            if heading:
                line = '#' * min(6, len(heading[1]) + 3) + line[len(heading[1]):]
        lines.append(line)
    return '\n'.join(lines)


def render_markdown(note, pdf_name, backgrounds, engine):
    if len(backgrounds) != len(note.pages):
        raise RenderError('Markdown note/background page count mismatch')
    md = [f'---\nsno2md_schema: {MARKDOWN_SCHEMA}\nmarkdown_engine: pymupdf4llm\n'
          f'generator_version: {GENERATOR_VERSION}\n---\n',
          f'# {note.title.replace(chr(10), " ").replace(chr(13), " ") or "Samsung Notes"}\n']
    for number, (pg, refs) in enumerate(zip(note.pages, backgrounds), 1):
        link = f'{quote(pdf_name, safe="")}#page={number}'
        md.extend([f'## 페이지 {number}\n', f'[필기 포함 PDF 페이지 열기]({link})\n'])
        handwriting, images = page_handwriting(pg)
        sources = []
        for _, _, info in refs:
            source = {key: value for key, value in info.items() if key not in ('text', 'pdf_page_number')}
            source.update(source_pdf_page=info['pdf_page_number'], extraction_scope='full_source_page')
            sources.append(source)
        metadata = {'note_page': number, 'source_refs': sources,
                    'handwriting': handwriting, 'inserted_image_count': images}
        # Escape comment delimiters in filenames; JSON decoding restores original values.
        payload = json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        payload = payload.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('--', '\\u002d\\u002d')
        md.append(f'<!-- sno2md:page-meta\n{payload}\n-->\n')
        for index, source in enumerate(sources, 1):
            text = engine.extract(source['asset'], source['source_pdf_page'] - 1)
            if text.strip():
                title = '원본 PDF 내용' if len(sources) == 1 else f'원본 PDF {index} · {source["source_pdf_page"]}페이지'
                md.extend([f'### {title}\n', nested_headings(text).strip() + '\n'])
    return '\n'.join(md)
