"""Decode supported SDOCX content once, independently of output format."""
from pathlib import Path, PurePosixPath
import struct
import math
import zipfile
import zlib
from .binary import Reader, load_input, note_header, page, sidecars
from .model import Note, DecodeError, Unsupported


def text_object(data, location):
    """Read TextCommon's leading text using record-local offsets, not a byte scan."""
    start = location['offset']
    r = Reader(data, start, start + location['length'])
    text = None
    while r.pos < r.end:
        offset = r.pos
        size, kind = r.read('I'), r.read('H')
        if size < 6:
            raise Unsupported('invalid note text record')
        r.take(size - 6)
        if kind != 7:
            continue
        h = Reader(data, offset + 6, offset + size)
        own = offset + h.read('I')
        h.take(h.read('B'))
        mask = int.from_bytes(h.take(h.read('B')), 'little')
        if not mask & 1:
            raise Unsupported('note text has no recognized TextCommon')
        common = Reader(data, own, offset + size)
        length = common.read('I')
        content = Reader(common.take(length))
        text = content.take(content.read('I') * 2).decode('utf-16-le')
    if text is None:
        raise Unsupported('note text structure is unsupported')
    return text


def subrecord(data, obj, kind):
    matches = [s for s in obj['subrecords'] if s['type'] == kind]
    if len(matches) != 1:
        raise Unsupported(f'expected one subrecord {kind}')
    sub = matches[0]
    return data[sub['offset']:sub['offset'] + sub['length']]


def shape_profile(data, obj):
    """Observed v4000 shape profile, derived directly from sample bytes.
    Path verbs: 1 move, 2 line, 3 quadratic, 4 cubic, 6 close. Unknown profiles fail.
    """
    b = subrecord(data, obj, 7)
    r = Reader(b, 6)
    own = r.read('I')
    flags = int.from_bytes(r.take(r.read('B')), 'little')
    mask = int.from_bytes(r.take(r.read('B')), 'little')
    kind = r.read('I')
    bounds = r.read('dddd')
    rotation = r.read('f')
    path_size = r.read('I')
    path = Reader(r.take(path_size))
    commands = []
    for _ in range(path.read('I')):
        verb = path.read('B')
        if verb in (1, 2):
            values = path.read('dd')
        elif verb == 3:
            values = path.read('dddd')
        elif verb == 4:
            values = path.read('dddddd')
        elif verb == 6:
            values = []
        else:
            raise Unsupported(f'unknown shape path verb {verb}')
        commands.append((verb, values))
    if path.pos != path.end or not math.isfinite(rotation) or any(
            not math.isfinite(v) for _, values in commands for v in values):
        raise Unsupported('unknown shape path layout')
    # Observed 0x1000 extension adds a trailing byte (02), after the same
    # length-delimited TextCommon/fill. Do not accept other extension payloads.
    extended = bool(mask & 0x1000)
    gap = b[r.pos:own]
    if obj['type'] == 7:
        expected_gap = 49 if kind == 2 else 33
        if (flags, mask & ~0x1000) != (4, 1) or kind not in (1, 2, 4, 88, 90) or len(gap) != expected_gap:
            raise Unsupported('unknown outline shape profile')
        # The shape has a TextCommon block; nonempty text must not be omitted.
        t = Reader(b, own)
        text = Reader(t.take(t.read('I')))
        if text.take(text.read('I') * 2).decode('utf-16-le').strip():
            raise Unsupported('text inside shape is not supported')
        if extended and b[t.pos:] != b'\x02':
            raise Unsupported('unknown outline extension')
        if not extended and t.pos != len(b):
            raise Unsupported('unknown outline tail')
        style = subrecord(data, obj, 6)
        style_at = struct.unpack_from('<I', style, 6)[0]
        s = style[style_at:]
        if (len(s) != 39 or s[:7] != bytes.fromhex('13000000010000')
                or s[11:23] != bytes(12) or s[23:27] != bytes.fromhex('0c000000')
                or s[31:] != bytes(8)):
            raise Unsupported('unknown outline style profile')
        return {'commands': commands, 'color': struct.unpack_from('<I', s, 7)[0],
                'width': struct.unpack_from('<f', s, 27)[0]}
    if (flags, mask & ~0x1000, kind) != (0, 32, 4) or gap != b'\0' or rotation:
        raise Unsupported('unknown inserted image profile')
    image_own = subrecord(data, obj, 3)
    if image_own != bytes.fromhex('1100000003000000000001000400000000'):
        raise Unsupported('cropped/styled image is not supported')
    fill = b[own:]
    if extended:
        if len(fill) != 68 or fill[-1:] != b'\x02':
            raise Unsupported('unknown image extension')
        fill = fill[:-1]
    if len(fill) != 67 or fill[:6] != bytes.fromhex('3e0000000200'):
        raise Unsupported('unknown image fill')
    if fill[10:34] != bytes(24) or struct.unpack_from('<fff', fill, 34) != (100.0, 100.0, 0.0):
        raise Unsupported('image fill transform/transparency is unsupported')
    style = subrecord(data, obj, 6)
    s = style[struct.unpack_from('<I', style, 6)[0]:]
    if s != bytes.fromhex('13000000010002000000ff0000000000000000000000000c000000000000000000000000000000'):
        raise Unsupported('unknown image layout/opacity')
    left, top, right, bottom = bounds
    expected = [(1, [left, top]), (2, [right, top]), (2, [right, bottom]), (2, [left, bottom]), (6, [])]
    if len(commands) != len(expected) or any(v != ev or len(xy) != len(exy) or
            any(abs(a-b) > 0.001 for a,b in zip(xy, exy)) for (v,xy),(ev,exy) in zip(commands,expected)):
        raise Unsupported('rotated/nonrectangular image is unsupported')
    return {'image_id': struct.unpack_from('<I', fill, 6)[0], 'bounds': bounds}


def png_pixels(data):
    """Decode non-interlaced 8-bit RGB/RGBA PNG for PDF embedding (stdlib only)."""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise Unsupported('only PNG inserted images are supported')
    pos, compressed, header = 8, bytearray(), None
    while pos < len(data):
        size = struct.unpack_from('>I', data, pos)[0]
        kind, chunk = data[pos+4:pos+8], data[pos+8:pos+8+size]
        crc = struct.unpack_from('>I', data, pos+8+size)[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != crc:
            raise ValueError('PNG CRC mismatch')
        pos += size + 12
        if kind == b'IHDR': header = struct.unpack('>IIBBBBB', chunk)
        elif kind == b'IDAT': compressed.extend(chunk)
        elif kind == b'tRNS': raise Unsupported('PNG tRNS transparency not supported')
        elif kind == b'IEND': break
    if header is None:
        raise ValueError('missing PNG IHDR')
    width, height, depth, color, compression, filter_method, interlace = header
    if depth != 8 or color not in (2,6) or compression or filter_method or interlace:
        raise Unsupported(f'unsupported PNG profile {header}')
    channels = 4 if color == 6 else 3
    stride = width * channels
    raw = zlib.decompress(compressed)
    if len(raw) != height * (stride + 1):
        raise ValueError('invalid PNG scanline length')
    previous = bytearray(stride)
    rgb, alpha = bytearray(), bytearray()
    for y in range(height):
        start = y * (stride + 1)
        method = raw[start]
        row = bytearray(raw[start+1:start+1+stride])
        if method not in range(5): raise Unsupported('unknown PNG filter')
        for i in range(stride):
            a = row[i-channels] if i >= channels else 0
            b = previous[i]
            c = previous[i-channels] if i >= channels else 0
            if method == 0: predictor = 0
            elif method == 1: predictor = a
            elif method == 2: predictor = b
            elif method == 3: predictor = (a+b)//2
            else:
                p = a+b-c
                distances = [abs(p-a), abs(p-b), abs(p-c)]
                predictor = (a,b,c)[distances.index(min(distances))]
            row[i] = (row[i] + predictor) & 255
        if channels == 3: rgb.extend(row)
        else:
            for i in range(0, stride, 4):
                rgb.extend(row[i:i+3]); alpha.append(row[i+3])
        previous = row
    return width, height, bytes(rgb), bytes(alpha)



def read_note(source):
    source = Path(source)
    try:
        files = load_input(source)
        for name in files:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                raise DecodeError(f'unsafe member path: {name}')
        order, bindings = sidecars(files)
        expected = {item['id'] + '.page' for item in order}
        if not order or len(expected) != len(order) or expected != {n for n in files if n.endswith('.page')}:
            raise Unsupported('empty, missing, duplicate, or unlisted note pages')
        metadata = note_header(files['note.note'])
        metadata['media_version'] = Reader(files['media/mediaInfo.dat']).read('I')
        title = text_object(files['note.note'], metadata['title_object'])
        if text_object(files['note.note'], metadata['body_object']).strip():
            raise Unsupported('nonblank note body text is not yet supported')
        media = {entry['id']: entry['filename'] for entry in bindings}
        if len(media) != len(bindings):
            raise DecodeError('duplicate media bind id')
        pages = []
        for index, item in enumerate(order, 1):
            try:
                raw = files[item['id'] + '.page']
                pg = page(raw)
                props = pg['properties']
                if pg['issues'] or props['unconsumed_hex'] or pg['section_tail_hex']:
                    raise Unsupported(f'undecoded page data: {pg["issues"]}')
                if pg['orientation'] or pg['offset_x'] or pg['offset_y'] or pg['text_only']:
                    raise Unsupported('nondefault note orientation/offset/text-only mode')
                if props.get('background_image') or props.get('background_rotation') or props.get('template_uri') or props.get('custom_objects'):
                    raise Unsupported('background image/template/custom object not supported')
                if not pg['width'] or not pg['height']:
                    raise DecodeError('invalid page dimensions')
                for bg in props.get('pdf', []):
                    if bg['file_id'] not in media:
                        raise DecodeError(f'unknown PDF media bind id {bg["file_id"]}')
                    bg['asset'] = 'media/' + media[bg['file_id']]
                    if bg['asset'] not in files: raise DecodeError('missing PDF asset')
                for layer in pg['layers']:
                    flags = bytes.fromhex(layer['flags_hex'])
                    layer['visible'] = not bool(flags[1] & 1)
                    if layer['visible'] and flags[3] & 3:
                        raise Unsupported('layer transparency/background is unsupported')
                    for obj in layer['objects']:
                        if obj['children']: raise Unsupported('grouped objects are unsupported')
                        if obj['type'] in (1, 15):
                            strokes = [s['stroke'] for s in obj['subrecords'] if 'stroke' in s]
                            if len(strokes) != 1: raise Unsupported('stroke geometry is missing/ambiguous')
                            obj['decoded'] = strokes[0]
                            if 'argb' not in obj['decoded'] or 'pen_size' not in obj['decoded']:
                                raise Unsupported('indirect pen color/size is unsupported')
                        elif obj['type'] in (3, 7):
                            obj['decoded'] = shape_profile(raw, obj)
                            if obj['type'] == 3:
                                if obj['decoded']['image_id'] not in media:
                                    raise DecodeError(f'unknown image media bind id {obj["decoded"]["image_id"]}')
                                obj['decoded']['asset'] = 'media/' + media[obj['decoded']['image_id']]
                                if obj['decoded']['asset'] not in files: raise DecodeError('missing image asset')
                        else: raise Unsupported(f'object type {obj["type"]} is unsupported')
                pages.append(pg)
            except (ValueError, KeyError, struct.error) as error:
                raise DecodeError(f'note page {index} ({item["id"]}): {error}') from error
        return Note(source=source, title=title, pages=pages, files=files, media=media, metadata=metadata)
    except DecodeError:
        raise
    except (ValueError, KeyError, struct.error, OSError, zipfile.BadZipFile) as error:
        raise DecodeError(f'cannot read note: {error}') from error
