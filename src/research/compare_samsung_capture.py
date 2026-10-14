"""Make a reference figure from a real Samsung screenshot and converted PDF.
Uses the historical presentation environment (PyMuPDF + Pillow), not converter deps.
"""
import argparse
from pathlib import Path
import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('screenshot', type=Path)
    root = Path(__file__).resolve().parents[2]
    parser.add_argument('--pdf', type=Path, default=root / 'private/output/converted/PSS.pdf')
    parser.add_argument('--output', type=Path, default=root / 'private/output/comparison-pss-page002')
    parser.add_argument('--width-scale', type=float, default=1.0, help='Caption only; must match the PDF conversion setting')
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(args.pdf) as pdf:
        page = pdf[1]
        # This sample page has one inserted slide image with handwriting on top.
        images = page.get_images()
        if len(images) != 1:
            raise ValueError('Expected the single slide image on PSS note page 2')
        rects = page.get_image_rects(images[0][0])
        if len(rects) != 1:
            raise ValueError('Expected one placement of the slide image')
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), clip=rects[0], alpha=False)
        pix.save(out / 'restored-slide.png')
    if not args.screenshot.is_file():
        print(f'Restored crop saved. Screenshot required: {args.screenshot}')
        return
    original = Image.open(args.screenshot).convert('RGB')
    restored = Image.open(out / 'restored-slide.png').convert('RGB')
    panel_width = 960
    panel_height = max(round(im.height * panel_width / im.width) for im in (original, restored))
    margin, gap, top, bottom = 32, 24, 110, 60
    board = Image.new('RGB', (margin*2 + panel_width*2 + gap, top+panel_height+bottom), '#f4f6f8')
    draw = ImageDraw.Draw(board)
    def font(size):
        return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', size)
    draw.text((margin, 20), 'Handwriting reconstruction | PSS note page 2', fill='#17212f', font=font(28))
    for i, (im, label) in enumerate(((original, 'Original | Samsung Notes screenshot'),
                                    (restored, 'Reconstructed | Constant-width PDF paths'))):
        x = margin+i*(panel_width+gap)
        draw.text((x, 72), label, fill='#344054', font=font(22))
        fitted = ImageOps.contain(im, (panel_width,panel_height), Image.Resampling.LANCZOS)
        board.paste(fitted,(x,top))
    draw.text((margin,top+panel_height+20), f'Width scale {args.width_scale:.1f}. Pressure and velocity are not applied. Images resized without stretching.', fill='#344054',font=font(18))
    board.save(out/'samsung-vs-restored.png')
    print(out/'samsung-vs-restored.png')


if __name__ == '__main__':
    main()
