#!/usr/bin/env python3
"""One-page research figure. PyMuPDF/Pillow are presentation-only dependencies.
Consumes our standalone SVG + original PDF + an already saved historical PNG.
Does not load Dietrich or any third-party SDOCX parser.
"""
import json
from pathlib import Path
import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'output/standalone-2026-10-14/jssc'
OUT = ROOT / 'output/comparison-jssc-page003'
page = json.loads((SOURCE / 'structure.json').read_text())['pages'][2]
reference = next((ROOT / 'output/2026-10-13').glob('JSSC*/preview_note_page_003.png'))
old = Image.open(reference).convert('RGB')

# Temporary in-memory PDF is a visualization canvas, not the production converter.
canvas = fitz.open()
width, height = page['width'], page['height']
dest = canvas.new_page(width=width, height=height)
for background in page['properties']['pdf']:
    with fitz.open(SOURCE / 'raw' / background['asset']) as pdf:
        dest.show_pdf_page(fitz.Rect(background['rect']), pdf, background['page_index'], keep_proportion=False)
with fitz.open(OUT / 'strokes.svg') as svg:
    with fitz.open('pdf', svg.convert_to_pdf()) as overlay:
        dest.show_pdf_page(dest.rect, overlay, 0, keep_proportion=False)
pix = dest.get_pixmap(matrix=fitz.Matrix(0.75, 0.75), alpha=False)
new = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
new.save(OUT / 'approximation.png')

def font(size):
    return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)


def board(left, right, subtitle, path, panel_width):
    def scaled(im):
        return im.resize((panel_width, round(im.height * panel_width / im.width)), Image.Resampling.LANCZOS)
    left, right = scaled(left), scaled(right)
    gap, margin, top, foot = 24, 30, 150, 85
    result = Image.new('RGB', (2 * panel_width + 2 * margin + gap, max(left.height, right.height) + top + foot), '#eef1f5')
    draw = ImageDraw.Draw(result)
    draw.text((margin, 18), 'SDOCX handwriting reconstruction | JSSC - note page 3', font=font(28), fill='#17212f')
    draw.text((margin, 58), subtitle, font=font(19), fill='#344054')
    draw.text((margin, 103), 'Previous Dietrich preview', font=font(24), fill='#17212f')
    draw.text((margin + panel_width + gap, 103), 'Own decoder + constant-width paths', font=font(24), fill='#17212f')
    result.paste(left, (margin, top))
    result.paste(right, (margin + panel_width + gap, top))
    y = top + max(left.height, right.height) + 18
    draw.text((margin, y), 'Left: saved research render, NOT a Samsung Notes screenshot.  Right: 5 strokes / 1,737 points.', font=font(17), fill='#344054')
    draw.text((margin, y + 28), 'Right width = stored pen_size (scale 1.0); round caps/joins; no pressure or velocity modulation.', font=font(17), fill='#344054')
    result.save(path)

board(old, new, 'Same source note and embedded PDF. Whole-page comparison.', OUT / 'comparison-full.png', 850)
# Identical note-coordinate crop on both images; independent pixel conversion.
crop_note = (240, 1180, 920, 1620)
def crop(im):
    x0, y0, x1, y1 = crop_note
    return im.crop((round(x0 * im.width / width), round(y0 * im.height / height),
                    round(x1 * im.width / width), round(y1 * im.height / height)))
board(crop(old), crop(new), 'Detail: same note-coordinate crop (240,1180)-(920,1620).', OUT / 'comparison-detail.png', 850)
metadata = {'sample': 'JSSC-SI-26-0608_Proof_hi_TriplerReview_260917_104914.sdocx',
            'note_page': 3, 'pdf_page': 3, 'reference_png': str(reference),
            'reference_is_samsung_screenshot': False, 'own_width_scale': 1.0,
            'reference_renderer_arguments': {'thickness_scale': 0.6, 'output_scale': 0.75},
            'own_svg_dependencies': 'Python standard library only',
            'figure_dependencies': ['PyMuPDF (PDF/SVG rasterization)', 'Pillow (figure layout)'],
            'crop_note_coordinates': crop_note, 'production_pdf_converter': False}
(OUT / 'comparison.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
print(OUT)
