"""Group contiguous IR regions without editing their text or reading order."""
import re
from statistics import median

from .layout_classifier import BODY, column_gutter

LIST_MARKER = re.compile(r'^( *)(?:[-+*]|\d{1,9}[.)]) +\S')


def group_page(page, profile):
    regions = page['regions']
    frame = page['bbox']
    width, height = frame[2] - frame[0], frame[3] - frame[1]
    gutter = profile['features']['gutter_x']
    if gutter is None:
        gutter = column_gutter(regions, width, height)
    def column(region):
        x0, _, x1, _ = region['bbox']
        if gutter is None:
            return 0
        return 0 if x1 <= gutter else 1 if x0 >= gutter else None

    def usable(region):
        x0, y0, x1, y1 = region['bbox']
        return (region['pos_status'] == 'valid' and region['text'].strip()
                and region['index'] == region['ordinal'] and x1 > x0 and y1 > y0)

    heights = [r['bbox'][3] - r['bbox'][1] for r in regions
               if r['class'] in BODY and usable(r)
               and len(r['text'].split()) <= 12 and r['bbox'][3] - r['bbox'][1] <= .06*height]
    line = median(heights) if heights else .025*height
    align = max(2, min(.007*width, .3*line))
    indent = max(.015*width, .8*line)
    markdown = page['raw_markdown']
    groups = []

    def emit(kind, rows, **evidence):
        groups.append(dict(id=f"{page['id']}.g{len(groups)+1}", type=kind,
                           members=[r['id'] for r in rows], evidence=evidence))

    def consecutive(a, b):
        stop, start = a['pos'][1], b['pos'][0]
        gap = b['bbox'][1] - a['bbox'][3]
        return (b['ordinal'] == a['ordinal'] + 1 and column(a) is not None
                and column(a) == column(b) and stop <= start and not markdown[stop:start].strip()
                and 0 <= gap <= 2.1*line)

    def conflicting_geometry(rows):
        members = {row['id'] for row in rows}
        for row in rows:
            x0, y0, x1, y1 = row['bbox']
            for other in regions:
                if other['id'] in members:
                    continue
                a, b, c, d = other['bbox']
                area = max(0, min(c, x1)-max(a, x0)) * max(0, min(d, y1)-max(b, y0))
                if area > .1 * (x1-x0) * (y1-y0):
                    return True
        return False

    i = 0
    while i < len(regions):
        first = regions[i]
        kind = first['class']
        if not usable(first):
            i += 1
            continue
        if kind in {'picture', 'table'} and i+1 < len(regions):
            caption = regions[i+1]
            a, b = first['bbox'], caption['bbox']
            overlap = min(a[2], b[2]) - max(a[0], b[0])
            if (caption['class'] == 'caption' and usable(caption)
                    and first['pos'][1] <= caption['pos'][0]
                    and not markdown[first['pos'][1]:caption['pos'][0]].strip()
                    and overlap >= .5*min(a[2]-a[0], b[2]-b[0]) and 0 <= b[1]-a[3] <= 2*line):
                emit('caption_pair', [first, caption], adjacent=True, horizontal_overlap=True)
                i += 2
                continue
        if kind not in BODY:
            i += 1
            continue
        rows = [first]
        for nxt in regions[i+1:]:
            if (nxt['class'] != kind or not usable(nxt) or not consecutive(rows[-1], nxt)
                    or (kind == 'text' and abs(first['bbox'][0]-nxt['bbox'][0]) > align)):
                break
            rows.append(nxt)
        if kind == 'list-item':
            levels = []
            anchors = []
            valid = True
            for r in rows:
                x = r['bbox'][0]
                if not anchors:
                    anchors.append(x)
                while len(anchors) > 1 and x < anchors[-1] - align:
                    anchors.pop()
                if x - anchors[-1] >= indent and x - anchors[-1] <= .12*width:
                    anchors.append(x)
                if abs(x - anchors[-1]) > align:
                    valid = False
                levels.append(len(anchors)-1)
            if not all(LIST_MARKER.match(r['text'].lstrip('\r\n')) for r in rows):
                valid = False
            if valid and any(levels) and conflicting_geometry(rows):
                valid = False
            emit('list', rows, levels=levels if valid else None, column=column(first))
        else:
            neighbors = regions[max(0, i-1):i] + regions[i+len(rows):i+len(rows)+1]
            same_column = [r for r in neighbors if usable(r) and r['class'] == 'text'
                           and column(r) is not None and column(r) == column(first)]
            x = first['bbox'][0]
            short = all(len(r['text'].split()) <= 24 for r in rows)
            relative = (same_column and all(x - r['bbox'][0] >= indent for r in same_column))
            gaps = [b['bbox'][1]-a['bbox'][3] for a,b in zip(rows,rows[1:])]
            consistent = not gaps or max(gaps)-min(gaps) <= .65*line
            # Adjacent text may be an independent label beside a figure.
            a = first['bbox']
            side_object = any(
                r['class'] == 'picture' and usable(r)
                and (a[0] >= r['bbox'][2]+align or a[2]+align <= r['bbox'][0])
                and min(a[3], r['bbox'][3])-max(a[1], r['bbox'][1]) >= .6*(a[3]-a[1])
                for r in neighbors
            )
            if len(rows) >= 2 and short and relative and consistent and not conflicting_geometry(rows):
                emit('indented', rows, aligned_left=True, relative_indent=True, consistent_spacing=True)
            elif (len(rows) == 1 and short and side_object and gutter is None
                  and len(first['text'].split()) >= 6 and a[3]-a[1] >= 2*line
                  and not conflicting_geometry(rows)):
                emit('visual_group', rows, beside_picture=True, multiple_line_height=True)
            else:
                emit('plain', rows)
        i += len(rows)
    return groups
