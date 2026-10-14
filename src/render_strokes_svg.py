#!/usr/bin/env python3
"""Render decoded SDOCX points as constant-width SVG paths (stdlib only)."""
import argparse
import json
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring


def objects(items):
    for item in items:
        yield item
        yield from objects(item['children'])


def render(page, width_scale):
    root = Element('svg', xmlns='http://www.w3.org/2000/svg',
                   width=str(page['width']), height=str(page['height']),
                   viewBox=f"0 0 {page['width']} {page['height']}")
    count = 0
    for layer in sorted(page['layers'], key=lambda item: item['number']):
        flags = bytes.fromhex(layer['flags_hex'])
        if flags[1] & 1:
            continue
        for obj in objects(layer['objects']):
            if obj['type'] not in (1, 15):
                raise ValueError(f"Unsupported object type {obj['type']}; cannot silently omit it")
            decoded = [sub['stroke'] for sub in obj['subrecords'] if 'stroke' in sub]
            if not decoded:
                raise ValueError('Stroke object has no decoded geometry')
            for stroke in decoded:
                color = stroke['argb']
                rgb, alpha = f'#{color & 0xffffff:06x}', (color >> 24) / 255
                width = stroke['pen_size'] * width_scale
                points = stroke['points']
                if len(points) == 1:
                    SubElement(root, 'circle', cx=str(points[0][0]), cy=str(points[0][1]),
                               r=str(width / 2), fill=rgb, opacity=str(alpha))
                else:
                    path = 'M ' + ' L '.join(f'{x:.6f} {y:.6f}' for x, y in points)
                    SubElement(root, 'path', d=path, fill='none', stroke=rgb,
                               **{'stroke-width': str(width), 'stroke-opacity': str(alpha),
                                  'stroke-linecap': 'round', 'stroke-linejoin': 'round'})
                count += 1
    return tostring(root, encoding='utf-8', xml_declaration=True), count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('structure', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--page', type=int, default=3, help='1-based note page')
    parser.add_argument('--width-scale', type=float, default=1.0)
    args = parser.parse_args()
    if args.width_scale <= 0:
        parser.error('--width-scale must be positive')
    data = json.loads(args.structure.read_text(encoding='utf-8'))
    if not 1 <= args.page <= len(data['pages']):
        parser.error('--page is outside the note')
    svg, count = render(data['pages'][args.page - 1], args.width_scale)
    with args.output.open('xb') as stream:
        stream.write(svg)
    print(f'{count} strokes rendered to {args.output}')


if __name__ == '__main__':
    main()
