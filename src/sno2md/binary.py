#!/usr/bin/env python3
"""Independent, standard-library-only SDOCX research reader; not a renderer."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import zipfile


class Reader:
    def __init__(self, data, pos=0, end=None):
        self.data, self.pos = data, pos
        self.end = len(data) if end is None else end

    def take(self, size):
        if size < 0 or self.pos + size > self.end:
            raise ValueError(f'out of bounds at {self.pos}: need {size}, end {self.end}')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def read(self, fmt):
        values = struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))
        return values[0] if len(values) == 1 else list(values)

    def string(self):
        return self.take(self.read('H') * 2).decode('utf-16-le')


def span(start, end):
    return {'offset': start, 'length': end - start}


def sidecars(files):
    r = Reader(files['pageIdInfo.dat'], 32)
    pages = [{'id': r.string(), 'check_hex': r.take(32).hex()} for _ in range(r.read('H'))]
    if r.pos != r.end:
        raise ValueError('unconsumed pageIdInfo bytes')
    r = Reader(files['media/mediaInfo.dat'])
    version, context = None, 'header'
    media, ids = [], set()
    try:
        version = r.read('I')
        count = r.read('H')
        # Match the observed record layout, not a version-number allowlist.
        # Unknown extensions must be investigated before they can be consumed.
        for index in range(count):
            context = f'record {index + 1} at byte {r.pos}'
            size = r.read('I')
            e = Reader(r.take(size))
            entry = {'id': e.read('I'), 'filename': e.string(),
                     'check_ascii': e.take(64).decode('ascii'), 'ref_count': e.read('H'),
                     'modified_raw': e.read('Q'), 'attached': e.read('B'),
                     'extra_hex': e.take(e.end - e.pos).hex()}
            if entry['extra_hex']:
                raise ValueError('unsupported extra media record data')
            if entry['id'] in ids:
                raise ValueError(f'duplicate media bind id {entry["id"]}')
            filename = entry['filename']
            if (not filename or filename in ('.', '..') or
                    any(c in filename for c in '/\\:\x00')):
                raise ValueError('invalid media filename')
            checksum = entry['check_ascii']
            if any(c not in '0123456789abcdefABCDEF' for c in checksum):
                raise ValueError('invalid media SHA-256 checksum encoding')
            if entry['attached'] not in (0, 1):
                raise ValueError(f'unsupported media attached flag {entry["attached"]}')
            asset = 'media/' + filename
            if asset not in files:
                raise ValueError(f'missing media asset: {asset}')
            if hashlib.sha256(files[asset]).hexdigest() != checksum.lower():
                raise ValueError(f'media checksum mismatch: {asset}')
            ids.add(entry['id'])
            media.append(entry)
        context = 'footer'
        if r.take(r.end - r.pos) != b'EOFX':
            raise ValueError('unexpected media footer')
    except (ValueError, struct.error) as error:
        raise ValueError(f'invalid mediaInfo.dat (version {version}, {context}): {error}') from error
    return pages, media


def note_header(data):
    r = Reader(data)
    result = {'integrity_offset': r.read('I'), 'constant1': r.read('B'),
              'header_flags': r.read('I'), 'constant2': r.read('B'),
              'property_flags': r.read('I'), 'version': r.read('I'), 'note_id': r.string()}
    for name, fmt in [('revision','I'), ('created_raw','Q'), ('modified_raw','Q'),
                      ('width','I'), ('height','I'), ('padding_x','I'), ('padding_y','I'), ('min_version','I')]:
        result[name] = r.read(fmt)
    for name in ('title_object', 'body_object'):
        size = r.read('I')
        result[name] = span(r.pos, r.pos + size)
        r.take(size)
    result['undecoded_tail'] = span(r.pos, r.end)
    return result


def signed_delta(value, scale):
    return (-1 if value & 0x8000 else 1) * (value & 0x7fff) / scale


def stroke(data, start, end):
    r = Reader(data, start + 6, end)
    flexible = start + r.read('I')
    flags = int.from_bytes(r.take(r.read('B')), 'little')
    mask = int.from_bytes(r.take(r.read('B')), 'little')
    count = r.read('H')
    if not count or not r.pos <= flexible <= end:
        raise ValueError('invalid stroke count/flexible offset')
    r.end = flexible
    if not flags & 1:
        raise ValueError('raw stroke geometry not yet implemented')
    points = [r.read('dd')]
    for _ in range(count - 1):
        points.append([points[-1][0] + signed_delta(r.read('H'), 32),
                       points[-1][1] + signed_delta(r.read('H'), 32)])
    def series(fmt, scale):
        values = [r.read(fmt)]
        for _ in range(count - 1):
            delta = r.read('H')
            values.append(values[-1] + (delta if scale is None else signed_delta(delta, scale)))
        return values
    result = {'flags': flags, 'flexible_mask': mask, 'points': points,
              'pressures': series('f', 128), 'timestamps': series('i', None)}
    if flags & 4:
        result['tilts'] = series('f', 32)
        result['orientations'] = series('f', 32)
    result['geometry_tail_hex'] = r.take(r.end - r.pos).hex()
    result['flexible_bytes'] = span(flexible, end)
    # Decode only the leading known fields; preserve all later properties verbatim.
    r = Reader(data, flexible, end)
    if mask & 1:
        result['style_status'] = 'unknown leading flexible field'
        return result
    if mask & 2:
        result['advanced_setting_id'] = r.read('I')
    if mask & 4:
        result['argb'] = r.read('I')
    if mask & 8:
        result['pen_size'] = r.read('f')
    result['style_tail_hex'] = r.take(r.end - r.pos).hex()
    return result


def object_record(r, issues):
    start = r.pos
    kind, children, size = r.read('B'), r.read('H'), r.read('I')
    if size < 32:
        raise ValueError(f'invalid object size at {start}')
    payload_start, payload_end = r.pos, r.pos + size - 32
    r.take(size)
    obj = {'type': kind, 'record': span(start, r.pos), 'payload': span(payload_start, payload_end),
           'subrecords': [], 'children': []}
    s = Reader(r.data, payload_start, payload_end)
    while s.pos < s.end:
        begin = s.pos
        length, subtype = s.read('I'), s.read('H')
        if length < 6:
            raise ValueError(f'invalid subrecord size at {begin}')
        s.take(length - 6)
        sub = {'type': subtype, **span(begin, s.pos)}
        if kind in (1, 15) and subtype == 1:
            try:
                sub['stroke'] = stroke(r.data, begin, s.pos)
            except (ValueError, struct.error) as error:
                sub['decode_error'] = str(error)
                issues.append(f'stroke at {begin}: {error}')
        obj['subrecords'].append(sub)
    if kind not in (1, 15):
        obj['status'] = 'framed but semantic decoding not implemented'
    obj['children'] = [object_record(r, issues) for _ in range(children)]
    return obj


def page(data):
    r = Reader(data)
    layer_at, property_at = r.read('I'), r.read('I')
    r.take(1)
    text_only = r.read('I')
    r.take(1)
    mask = r.read('I')
    result = {'property_mask': mask, 'text_only': text_only, 'issues': []}
    for key in ('orientation', 'width', 'height', 'offset_x', 'offset_y'):
        result[key] = r.read('I')
    result.update(id=r.string(), modified_raw=r.read('Q'), version=r.read('I'), min_version=r.read('I'))
    if result['version'] != 4000 or not r.pos <= property_at <= layer_at <= len(data) - 58:
        raise ValueError('unsupported page version or invalid section offsets')
    p = Reader(data, property_at, layer_at)
    props = {}
    if mask & ~0x41fff:
        result['issues'].append(f'unknown property bits: {mask:#x}; property block left opaque')
    else:
        if mask & 1: props['drawn_rect'] = p.read('dddd')
        if mask & 2: props['tags'] = [p.string() for _ in range(p.read('H'))]
        if mask & 4: props['template_uri'] = p.string()
        for bit, key in [(8,'background_image'), (16,'background_mode'), (32,'background_color'),
                         (64,'background_width'), (128,'background_rotation')]:
            if mask & bit: props[key] = p.read('I')
        if mask & 256:
            props['pdf'] = [{'file_id': p.read('i'), 'page_index': p.read('i'), 'rect': p.read('iiii')}
                            for _ in range(p.read('H'))]
        if mask & 512: props['template_type'] = p.read('I')
        if mask & 1024:
            count, size = p.read('I'), p.read('H')
            props['cache_records'] = [p.take(size).hex() for _ in range(count)]
        if mask & 2048: props['imported_height'] = p.read('I')
        if mask & 4096: props['reserved_hex'] = p.take(4).hex()
        if mask & 0x40000:
            props['custom_objects'] = [{'type': p.read('I'), 'bytes_hex': p.take(p.read('I')).hex()}
                                       for _ in range(p.read('I'))]
    props['unconsumed_hex'] = p.take(p.end - p.pos).hex()
    result['properties'] = props
    r = Reader(data, layer_at, len(data) - 58)
    layer_count, current = r.read('H'), r.read('H')
    result.update(current_layer=current, layers=[])
    for _ in range(layer_count):
        start = r.pos
        header_size, metadata_at = r.read('I'), r.read('I')
        constants_and_flags = r.take(4).hex()
        number = r.read('I')
        r.take(header_size - (r.pos - start))
        count = r.read('I')
        objects = [object_record(r, result['issues']) for _ in range(count)]
        result['layers'].append({'number': number, 'metadata_offset': metadata_at,
                                 'flags_hex': constants_and_flags, 'objects': objects,
                                 'check_hex': r.take(32).hex()})
    result['section_tail_hex'] = r.take(r.end - r.pos).hex()
    result['footer_hex'] = data[-26:].hex()
    return result


def walk(objects):
    for obj in objects:
        yield obj
        yield from walk(obj['children'])


def load_input(source):
    if source.is_dir():
        paths = [p for p in source.rglob('*') if p.is_file()]
        if any(p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) for p in paths):
            raise ValueError('symlinks outside a note are not supported')
        return {p.relative_to(source).as_posix(): p.read_bytes() for p in paths}
    with zipfile.ZipFile(source) as archive:
        members = [i for i in archive.infolist() if not i.is_dir()]
        if len({i.filename for i in members}) != len(members):
            raise ValueError('duplicate ZIP member names')
        return {i.filename: archive.read(i) for i in members}


def inspect(source, output):
    files = load_input(source)
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
            raise ValueError(f'unsafe member path: {name}')
    # Refuse existing destinations so research runs cannot overwrite evidence.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        target = output / 'raw' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    order, media = sidecars(files)
    media_by_id = {entry['id']: entry for entry in media}
    pages = []
    for entry in order:
        name = entry['id'] + '.page'
        decoded = page(files[name])
        decoded['source'] = name
        for background in decoded['properties'].get('pdf', []):
            binding = media_by_id.get(background['file_id'])
            asset = 'media/' + binding['filename'] if binding else None
            background['asset'] = asset
            if asset not in files:
                decoded['issues'].append(f'missing PDF asset for bind id {background["file_id"]}')
        pages.append(decoded)
    types, strokes, points = Counter(), 0, 0
    for pg in pages:
        for layer in pg['layers']:
            for obj in walk(layer['objects']):
                types[obj['type']] += 1
                for sub in obj['subrecords']:
                    if 'stroke' in sub:
                        strokes += 1
                        points += len(sub['stroke']['points'])
    result = {'note': note_header(files['note.note']), 'page_order': order, 'media': media,
              'media_version': Reader(files['media/mediaInfo.dat']).read('I'),
              'pages': pages, 'unlisted_pages': sorted(set(n for n in files if n.endswith('.page')) -
                                                      {p['source'] for p in pages}),
              'inventory': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                            for name, data in sorted(files.items())}}
    summary = {'pages': len(pages), 'pdf_backgrounds': sum(len(p['properties'].get('pdf', [])) for p in pages),
               'object_types': dict(types), 'decoded_strokes': strokes, 'points': points,
               'issues': [f'{p["source"]}: {issue}' for p in pages for issue in p['issues']],
               'framed_only_objects': sum(n for kind, n in types.items() if kind not in (1, 15)),
               'render_ready': False}
    result['summary'] = summary
    (output / 'structure.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({'input': str(source), **summary}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='one .sdocx or one unpacked note root')
    parser.add_argument('output', type=Path, help='new output directory')
    args = parser.parse_args()
    inspect(args.source, args.output)


if __name__ == '__main__':
    main()
