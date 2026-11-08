"""Plan and apply reversible Markdown insertions in verified source ranges."""
import re

from .visual_grouping import LIST_MARKER

MODES = ('classic', 'auto', 'flow', 'canvas')
VERSION = 'layout-md-v5'
LINE_STYLES = (
    ('breaks', 'paragraph_breaks', '\n\n', '\n>\n> '),
    ('row_breaks', 'visual_row_breaks', '  \n', '  \n> '),
)
UNSAFE_BLOCK = re.compile(
    r'^\s*(?:[#>|]|[-+*]\s|\d+[.)]\s|`{3,}|~{3,}|\$\$|<[/!]|'
    r'<(?:div|table|pre|details)\b|(?:-{3,}|_{3,}|\*{3,})\s*$)', re.I)


def _project(span, edits):
    start, stop = span
    delta = 0
    for edit in edits:
        a, b = edit['span']
        if b <= start:
            delta += (edit['output_span'][1] - edit['output_span'][0]) - (b-a)
        elif a < stop:
            raise ValueError('candidate intersects an earlier replacement')
        else:
            break
    return start + delta, stop + delta


def _safe_order(regions):
    cursor = 0
    for region in regions:
        if region['pos_status'] != 'valid':
            continue
        start, stop = region['pos']
        if start < cursor or region['index'] != region['ordinal']:
            return False
        cursor = stop
    return True


def render_page(page, profile, groups, mode, classic, object_records=(), hidden_records=(),
                line_plans=None):
    policy = profile['layout'] if mode == 'auto' else mode
    audit = dict(policy=policy, groups=[], fallback=None)
    if policy in ('classic', 'unknown'):
        if policy == 'unknown':
            audit['fallback'] = 'uncertain_page_layout'
        return classic, audit
    regions = page['regions']
    if not _safe_order(regions):
        audit['fallback'] = 'conflicting_source_order_or_ranges'
        return classic, audit
    raw = page['raw_markdown']
    if re.search(r'^\s*(?:`{3,}|~{3,}|\$\$|<!--|<(?:div|table|pre|details)\b)', classic, re.M | re.I):
        audit['fallback'] = 'ambiguous_block_context'
        return classic, audit
    objects = sorted((r for r in object_records if r['action'] in {'image', 'duplicate_omitted'}),
                     key=lambda r: r['span'])
    by_id = {region['id']: region for region in regions}
    insertions = []
    line_plans = line_plans or {}
    if policy in {'canvas', 'mixed'}:
        for group in groups:
            if group['type'] != 'plain':
                continue
            for member in group['members']:
                plan = line_plans.get(member)
                if not plan:
                    continue
                region = by_id[member]
                base = region['pos'][0]
                if 'span' in plan:
                    span = [base+at for at in plan['span']]
                    try:
                        start, stop = _project(_project(span, objects), hidden_records)
                        if raw[slice(*span)] != classic[start:stop] or not 0 <= start < stop <= len(classic):
                            raise ValueError('line group differs from its source slice')
                    except ValueError as error:
                        audit['groups'].append(dict(id=group['id'], member=member, fallback=str(error)))
                        continue
                    audit['groups'].append(dict(id=group['id'], member=member, source_span=span,
                                                source_rows=[[base+a, base+b] for a,b in plan['rows']],
                                                classic_span=[start, stop], applied='definition_lines',
                                                indent=plan['indent']))
                    insertions.append((start, stop, plan['replacement'], group['id']))
                for key, action, separator, _ in LINE_STYLES:
                    restored = []
                    for boundary in plan.get(key, ()):
                        before = base + boundary
                        try:
                            a, b = _project(_project([before-1, before+1], objects), hidden_records)
                        except ValueError:
                            continue
                        if raw[before-1:before+1] == classic[a:b]:
                            insertions.append((a+1, a+1, separator, group['id']))
                            restored.append(before)
                    if restored:
                        audit['groups'].append(dict(id=group['id'], member=member,
                                                    source_boundaries=restored, applied=action))
    for group in groups:
        kind = group['type']
        levels = group['evidence'].get('levels')
        callout = policy in {'canvas', 'mixed'} and kind in {'indented', 'visual_group'}
        if not callout and not (kind == 'list' and levels and any(levels)):
            continue
        rows = [by_id[key] for key in group['members']]
        edits = []
        try:
            span = [rows[0]['pos'][0], rows[-1]['pos'][1]]
            start, stop = _project(_project(span, objects), hidden_records)
            value = raw[slice(*span)]
            if not 0 <= start < stop <= len(classic) or value != classic[start:stop]:
                raise ValueError('candidate differs from its source slice')
            if callout:
                # Trim only the address of the edit. Keep all original whitespace.
                first = start + len(value) - len(value.lstrip('\r\n'))
                body = value.strip('\r\n')
                if not body or '![' in body or any(
                    UNSAFE_BLOCK.match(line) or line.startswith(('    ', '\t')) for line in body.splitlines()
                ):
                    raise ValueError('candidate contains existing Markdown block syntax')
                if first and classic[first-1] != '\n':
                    raise ValueError('candidate does not start at a line boundary')
                last = first + len(body)
                if last < len(classic) and classic[last] not in '\r\n':
                    raise ValueError('candidate does not end at a line boundary')
                prefix = '\n' if first and not classic.endswith('\n\n', 0, first) else ''
                edits.append((first, prefix + '> [!note]\n> '))
                for match in re.finditer('\n', body):
                    edits.append((first+match.end(), '> '))
                for row in rows:
                    row_start = row['pos'][0] - span[0] + start
                    for key, _, _, separator in LINE_STYLES:
                        for boundary in line_plans.get(row['id'], {}).get(key, ()):
                            at = row_start + boundary
                            if first < at < last:
                                edits.append((at, separator))
                if last < len(classic) and not classic[last:].startswith(('\n\n', '\r\n\r\n')):
                    edits.append((last, '\n'))
            else:
                if any(row['text'].lstrip('\r\n').startswith((' ', '\t')) for row in rows):
                    audit['groups'].append(dict(id=group['id'], preserved='native_list_indentation'))
                    continue
                markers = []
                for row in rows:
                    body = row['text'].strip('\r\n')
                    lines = body.splitlines()
                    marker = LIST_MARKER.match(lines[0]) if lines else None
                    if not marker or len(lines) != 1 or '![' in body:
                        raise ValueError('list continuation or marker is ambiguous')
                    offset = start + row['pos'][0] - span[0] + len(row['text']) - len(row['text'].lstrip('\r\n'))
                    if offset and classic[offset-1] != '\n':
                        raise ValueError('list marker is not at a line boundary')
                    token = lines[0].split()[0]
                    markers.append(len(token)+1)
                    if token.endswith(')'):
                        raise ValueError('unsupported list marker')
                    level = levels[len(markers)-1]
                    if level:
                        edits.append((offset, ' ' * (4*level if max(markers) <= 4 else max(markers)*level)))
                if len({len(row['text'].strip().split()[0]) for row in rows}) != 1:
                    raise ValueError('mixed marker widths')
        except ValueError as error:
            audit['groups'].append(dict(id=group['id'], fallback=str(error)))
            continue
        audit['groups'].append(dict(id=group['id'], source_span=span, classic_span=[start, stop],
                                    applied='callout' if callout else 'nested_list'))
        insertions.extend((at, at, value, group['id']) for at, value in edits)
    output, cursor, delta = [], 0, 0
    audit['insertions'] = []
    for at, stop, value, group_id in sorted(insertions, key=lambda edit: edit[0]):
        if at < cursor:
            return classic, dict(policy=policy, groups=[], fallback='overlapping_layout_edits')
        output.extend((classic[cursor:at], value))
        record = dict(classic_offset=at, output_span=[at+delta, at+delta+len(value)], group=group_id)
        if stop > at:
            record['original'] = classic[at:stop]
        audit['insertions'].append(record)
        cursor = stop
        delta += len(value) - (stop-at)
    output.append(classic[cursor:])
    return ''.join(output), audit
