"""Markdown generated directly from the in-memory decoded note."""
import re
from urllib.parse import quote


def text_block(text):
    # Preserve literal extracted text, including embedded Markdown and backticks.
    runs = re.findall(r'`+', text)
    fence = '`' * max(3, max((len(r)+1 for r in runs), default=3))
    return f'{fence}text\n{text}' + ('' if text.endswith('\n') else '\n') + f'{fence}\n'


def render_markdown(note, pdf_name, backgrounds):
    md = [f'# {note.title.replace(chr(10), " ").replace(chr(13), " ") or "Samsung Notes"}\n']
    for number, (pg, refs) in enumerate(zip(note.pages, backgrounds), 1):
        link = f'{quote(pdf_name, safe="")}#page={number}'
        md.extend([f'## 페이지 {number}\n', f'[PDF 페이지 열기]({link})\n'])
        strokes = images = 0
        for layer in pg['layers']:
            if not layer['visible']:
                continue
            for obj in layer['objects']:
                if obj['type'] in (1, 15):
                    strokes += 1
                elif obj['type'] == 3:
                    images += 1
        if strokes:
            md.append(f'필기: {strokes}획.\n')
        for i in range(1, images + 1):
            md.append(f'- [삽입 이미지 {i}]({link})')
        if images:
            md.append('')
        for _, _, info in refs:
            if info['text'].strip():
                md.append(text_block(info['text']))
    return '\n'.join(md)
