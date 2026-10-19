"""Visible stroke geometry only: no OCR, grouping, or semantic interpretation."""
import math
from .model import RenderError


def page_handwriting(page):
    width, height = page['width'], page['height']
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise RenderError('invalid note dimensions for handwriting metadata')
    boxes, images = [], 0
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
                xs, ys = zip(*points)
                # Preserve out-of-page geometry; clipping would change the recorded stroke.
                box = [min(xs)/width, min(ys)/height, max(xs)/width, max(ys)/height]
                if not all(math.isfinite(v) for v in box):
                    raise RenderError('non-finite normalized stroke bounds')
                boxes.append(box)
    return {'stroke_count': len(boxes), 'bbox_kind': 'individual_stroke_geometry',
            'coordinate_system': 'note_page_normalized_top_left',
            'clipping': 'none', 'stroke_bboxes': boxes}, images
