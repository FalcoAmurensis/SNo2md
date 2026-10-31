"""Compact visible stroke counts; no OCR or semantic interpretation."""
import math
from .model import RenderError


def page_handwriting(page):
    width, height = page['width'], page['height']
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise RenderError('invalid note dimensions for handwriting metadata')
    strokes, images = 0, 0
    for layer in page['layers']:
        if not layer['visible']:
            continue
        for obj in layer['objects']:
            if obj['type'] == 3:
                images += 1
            elif obj['type'] in (1, 15):
                points = obj['decoded']['points']
                if not points or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
                    raise RenderError('invalid stroke points for handwriting metadata')
                strokes += 1
    return {'stroke_count': strokes}, images
