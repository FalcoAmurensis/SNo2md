"""Recover PDF line boundaries and aligned definition rows inside Layout text."""
import re
from statistics import median


_MARKUP = re.compile(r'</?(?:sup|sub|u)>|\*\*|(?<!\w)_(?=\w)|(?<=\w)_(?!\w)')
_CLOSING_TAGS = re.compile(r'(?:</(?:sup|sub|u)>)+')
_DEFINITION = re.compile(r'^([^\s:]{1,8})\s*:\s*\S')
_BLOCK_START = re.compile(r'^ *(?:[#>|]|[-+*]\s|\d+[.)]\s|[-*_]{3,}\s*$)')
_LABEL = re.compile(r'^[^\W\d_][\w-]*(?:[ \t]+[\w-]+){0,4}\s*:\s+\S')


def _compact(text):
    """Ignore known inline markup and whitespace only, keeping character addresses."""
    chars, offsets = [], []
    cursor = 0
    for token in _MARKUP.finditer(text):
        for index in range(cursor, token.start()):
            if not text[index].isspace():
                chars.append(text[index])
                offsets.append(index)
        cursor = token.end()
    for index in range(cursor, len(text)):
        if not text[index].isspace():
            chars.append(text[index])
            offsets.append(index)
    return ''.join(chars), offsets


def _lines(blocks, bbox):
    x0, y0, x1, y1 = bbox
    rows = []
    for block_index, block in enumerate(blocks):
        for line in block.get('lines', ()):
            if line.get('dir') != (1.0, 0.0):
                continue
            spans = [span for span in line['spans'] if span['text'].strip()]
            if not spans:
                continue
            largest = max(spans, key=lambda span: span['size'])
            x = min(span['origin'][0] for span in spans)
            y = largest['origin'][1]
            if x0-2 <= x <= x1 and y0 <= y <= y1+2 and line['bbox'][2] <= x1+2:
                text = ''.join(s['text'] for s in line['spans']).strip()
                rows.append((x, y, largest['size'], text, line['bbox'][2], block_index))
    return sorted(rows, key=lambda row: row[1])


def _unique_offset(text, fragment):
    at = text.find(fragment)
    return at if at >= 0 and text.find(fragment, at+1) < 0 else None


def _ends_sentence(text):
    return text.rstrip('”’»"`)）]').endswith(('.', '!', '?', '。', '！', '？'))


def _continuation_spacing(lines, index, size):
    """Use nearby wrapped lines with comparable size and alignment as a reference."""
    gaps = []
    neighborhood = lines[max(0, index-3):index+5]
    for a, b in zip(neighborhood, neighborhood[1:]):
        gap = b[1] - a[1]
        if (not _ends_sentence(a[3]) and abs(a[0]-b[0]) <= .35*size
                and all(.8*size <= row[2] <= 1.2*size for row in (a, b))
                and .65*size <= gap <= 2.4*size):
            gaps.append(gap)
    return median(gaps) if gaps else None


def _authored_rows(a, b, size, step):
    if a[5] == b[5] or abs(a[0]-b[0]) > .35*size or min(a[2], b[2]) < .8*size:
        return False
    if step >= 1.7*size and all(_LABEL.match(row[3]) for row in (a, b)):
        return True
    # A repeated column gap with an assignment on both sides is evidence for
    # parallel equation rows, even when they have no sentence punctuation.
    columns = [re.split(r' {4,}', row[3], maxsplit=1) for row in (a, b)]
    return abs(a[4]-b[4]) <= size and all(
        len(row) == 2 and all('=' in cell for cell in row) for row in columns
    )


def _line_breaks(text, compact, offsets, lines, right, baseline=float('inf'), limit=None):
    """Map paragraphs and authored rows while leaving ordinary wraps to the reader."""
    breaks = []
    row_breaks = []
    limit = len(text) if limit is None else limit
    for index, (a, b) in enumerate(zip(lines, lines[1:])):
        step = b[1] - a[1]
        indent = b[0] - a[0]
        size = max(a[2], b[2])
        if b[1] >= baseline or abs(indent) > 1.5*size or not .65*size <= step <= 3*size:
            continue
        authored = _authored_rows(a, b, size, step)
        if not authored:
            next_char = b[3].lstrip('“‘«"\'([{（')[:1]
            if not _ends_sentence(a[3]) or not next_char.isalpha() or next_char.islower():
                continue
            typical = _continuation_spacing(lines, index, size)
            spaced = step >= (max(1.7*size, 1.4*typical) if typical is not None else 2.2*size)
            indented = indent >= .8*size and right-a[4] >= size
            if not spaced and not indented:
                continue
        left, right = a[3].split(), b[3].split()
        # Full source lines also support short labels and scripts without spaces.
        anchors = [(left, right)]
        if not authored and min(len(left), len(right)) >= 4 and all(
            any(c.isalpha() for c in word) for word in left[-4:]+right[:4]
        ):
            anchors.append((left[-4:], right[:4]))
        for tail, head in anchors:
            before, after = ''.join(tail), ''.join(head)
            if not before or not after or not any(c.isalpha() for c in before+after):
                continue
            at = _unique_offset(compact, before+after)
            if at is None:
                continue
            end = offsets[at+len(before)-1]+1
            start = offsets[at+len(before)]
            gap = text[end:start]
            following = text[start:].split('\n', 1)[0]
            if (start < limit and gap and set(gap) == {' '} and len(gap) <= 3
                    and not _BLOCK_START.match(following)):
                (row_breaks if authored else breaks).append(end)
                break
    return dict(breaks=sorted(set(breaks)), row_breaks=sorted(set(row_breaks)))


def definition_plan(region, blocks):
    """Return a local edit only when every member maps uniquely and exactly."""
    text = region['text']
    if '\n' in text.strip() or '![' in text or '\x00' in text:
        return None
    lines = _lines(blocks, region['bbox'])
    rows = []
    for row in lines:
        match = _DEFINITION.match(row[3])
        if match and match[1].isalnum():
            rows.append(row)
    if len(rows) < 3:
        return None
    size = median(row[2] for row in rows)
    left = min(row[0] for row in rows)
    if left - region['bbox'][0] < size or any(abs(row[0]-left) > size*.35 for row in rows):
        return None
    if any(not .7*size <= b[1]-a[1] <= 2.3*size for a,b in zip(rows, rows[1:])):
        return None
    if any(rows[0][1] < line[1] < rows[-1][1] and line not in rows for line in lines):
        return None
    compact, offsets = _compact(text)
    ranges = []
    for _, _, _, source, _, _ in rows:
        # PDF span text has no Markdown markup. Normalize only its whitespace.
        key = ''.join(source.split())
        at = _unique_offset(compact, key)
        if at is None:
            return None
        start, stop = offsets[at], offsets[at+len(key)-1]+1
        closing = _CLOSING_TAGS.match(text, stop)
        if closing:
            stop = closing.end()
        if (start and not text[start-1].isspace()) or (stop < len(text) and not text[stop].isspace()):
            return None
        ranges.append((start, stop))
    ordered = sorted(ranges)
    if any(end >= start or not text[end:start].isspace()
           for (_, end), (start, _) in zip(ordered, ordered[1:])):
        return None
    start, stop = ordered[0][0], ordered[-1][1]
    if text[stop:].strip():
        return None
    indent = '&nbsp;' * max(2, min(8, round((left-region['bbox'][0])/size)))
    members = [text[a:b] for a,b in ranges]
    if any(row.count('**') % 2 or any(row.count(f'<{tag}>') != row.count(f'</{tag}>')
                                     for tag in ('sup', 'sub', 'u')) for row in members):
        return None
    return dict(span=[start, stop], replacement='\n\n' + '  \n'.join(indent+row for row in members),
                rows=[list(span) for span in ranges], indent=indent.count('&nbsp;'),
                **_line_breaks(text, compact, offsets, lines, region['bbox'][2], rows[0][1], start))


def canvas_line_plans(page, pdf, pdf_page):
    candidates = [r for r in page['regions'] if r['class'] == 'text' and r['pos_status'] == 'valid'
                  and r['text'].strip() and not any(token in r['text'] for token in ('![', '](', '`', '\x00'))]
    if not candidates or pdf_page.rotation:
        return {}
    blocks = pdf_page.get_text('dict', flags=pdf.TEXTFLAGS_DICT & ~pdf.TEXT_PRESERVE_IMAGES)['blocks']
    plans = {}
    for region in candidates:
        text = region['text']
        definition = definition_plan(region, blocks) if text.count(':') >= 3 else None
        if definition:
            plans[region['id']] = definition
            continue
        lines = _lines(blocks, region['bbox'])
        if len(lines) >= 2:
            compact, offsets = _compact(text)
            breaks = _line_breaks(text, compact, offsets, lines, region['bbox'][2])
            if any(breaks.values()):
                plans[region['id']] = breaks
    return plans
