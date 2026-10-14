#!/usr/bin/env python3
"""Convert one SDOCX/unpacked note into a PDF using our decoder and pypdf.
Supported profiles: v4000 compact strokes, observed outline shapes and plain PNG images.
Unsupported content is an error; never publish a silently incomplete PDF.
"""
import argparse
from collections import Counter
from io import BytesIO
import math
import os
from pathlib import Path
import struct
import tempfile
import zlib

from pypdf import PdfReader, PdfWriter, PageObject, Transformation
from pypdf.generic import (DictionaryObject, NameObject, NumberObject,
                          FloatObject, DecodedStreamObject)
from inspect_sdocx import Reader, load_input, note_header, page, sidecars


class Unsupported(ValueError):
    pass


def number(value):
    if not math.isfinite(value):
        raise Unsupported('non-finite geometry')
    return f'{value:.6f}'.rstrip('0').rstrip('.') or '0'


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
    Path verbs: 1 move, 2 line, 3 quadratic, 6 close. Unknown profiles fail.
    """
    b = subrecord(data, obj, 7)
    r = Reader(b, 6)
    own = r.read('I')
    flags = int.from_bytes(r.take(r.read('B')), 'little')
    mask = int.from_bytes(r.take(r.read('B')), 'little')
    kind = r.read('I')
    bounds = r.read('dddd')
    reserved = r.read('I')
    path_size = r.read('I')
    path = Reader(r.take(path_size))
    commands = []
    for _ in range(path.read('I')):
        verb = path.read('B')
        if verb in (1, 2):
            values = path.read('dd')
        elif verb == 3:
            values = path.read('dddd')
        elif verb == 6:
            values = []
        else:
            raise Unsupported(f'unknown shape path verb {verb}')
        commands.append((verb, values))
    if path.pos != path.end or reserved:
        raise Unsupported('unknown shape path layout')
    if obj['type'] == 7:
        if (flags, mask) != (4, 1) or kind not in (1, 4, 88) or own - r.pos != 33:
            raise Unsupported('unknown outline shape profile')
        # The shape has a TextCommon block; nonempty text must not be omitted.
        t = Reader(b, own)
        text = Reader(t.take(t.read('I')))
        if text.take(text.read('I') * 2).decode('utf-16-le').strip():
            raise Unsupported('text inside shape is not supported')
        style = subrecord(data, obj, 6)
        style_at = struct.unpack_from('<I', style, 6)[0]
        s = style[style_at:]
        if (len(s) != 39 or s[:7] != bytes.fromhex('13000000010000')
                or s[11:23] != bytes(12) or s[23:27] != bytes.fromhex('0c000000')
                or s[31:] != bytes(8)):
            raise Unsupported('unknown outline style profile')
        return {'commands': commands, 'color': struct.unpack_from('<I', s, 7)[0],
                'width': struct.unpack_from('<f', s, 27)[0]}
    if (flags, mask, kind) != (0, 32, 4) or own != r.pos + 1:
        raise Unsupported('unknown inserted image profile')
    image_own = subrecord(data, obj, 3)
    if image_own != bytes.fromhex('1100000003000000000001000400000000'):
        raise Unsupported('cropped/styled image is not supported')
    fill = b[own:]
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


def stream_object(data):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return stream


def image_object(writer, data):
    width, height, rgb, alpha = png_pixels(data)
    def make(pixels, color):
        obj = stream_object(pixels).flate_encode()
        obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                    NameObject('/Width'): NumberObject(width), NameObject('/Height'): NumberObject(height),
                    NameObject('/ColorSpace'): NameObject(color), NameObject('/BitsPerComponent'): NumberObject(8)})
        return obj
    obj = make(rgb, '/DeviceRGB')
    if alpha:
        obj[NameObject('/SMask')] = writer._add_object(make(alpha, '/DeviceGray'))
    return writer._add_object(obj)


def overlay(writer, pg, raw, media, files, width_scale, counters):
    out = PageObject.create_blank_page(width=pg['width'], height=pg['height'])
    states, images = DictionaryObject(), DictionaryObject()
    commands = [f"q 1 0 0 -1 0 {pg['height']} cm"]
    def style(color, width):
        if not math.isfinite(width) or width <= 0:
            raise Unsupported('invalid pen width')
        alpha = (color >> 24) / 255
        name = NameObject(f'/A{color >> 24}')
        states[name] = DictionaryObject({NameObject('/Type'): NameObject('/ExtGState'),
                                        NameObject('/CA'): FloatObject(alpha), NameObject('/ca'): FloatObject(alpha)})
        rgb = ' '.join(number((color >> shift & 255)/255) for shift in (16,8,0))
        commands.append(f'q {name} gs {rgb} RG {rgb} rg {number(width)} w 1 J 1 j')
    for layer in sorted(pg['layers'], key=lambda l: l['number']):
        flags = bytes.fromhex(layer['flags_hex'])
        if flags[1] & 1:
            continue
        if flags[3] & 3:
            raise Unsupported('layer transparency/background is unsupported')
        for obj in layer['objects']:
            if obj['children']:
                raise Unsupported('grouped objects are unsupported')
            kind = obj['type']
            if kind in (1,15):
                strokes = [s['stroke'] for s in obj['subrecords'] if 'stroke' in s]
                if len(strokes) != 1: raise Unsupported('stroke geometry is missing/ambiguous')
                s = strokes[0]
                if 'argb' not in s or 'pen_size' not in s:
                    raise Unsupported('indirect pen color/size is unsupported')
                style(s['argb'], s['pen_size'] * width_scale)
                points = s['points']
                commands.append(f'{number(points[0][0])} {number(points[0][1])} m')
                for x,y in points[1:]: commands.append(f'{number(x)} {number(y)} l')
                if len(points) == 1:
                    # A zero-length round-capped segment is a dot in PDF.
                    commands.append(f'{number(points[0][0])} {number(points[0][1])} l')
                commands.append('S Q')
                counters['strokes'] += 1
            elif kind == 7:
                shape = shape_profile(raw, obj)
                style(shape['color'], shape['width'] * width_scale)
                current = None
                for verb, xy in shape['commands']:
                    if verb in (1,2):
                        commands.append(f'{number(xy[0])} {number(xy[1])} {"m" if verb == 1 else "l"}')
                        current = xy
                    elif verb == 3:
                        if current is None: raise Unsupported('quadratic without current point')
                        qx,qy,x,y = xy
                        c1 = [current[0]+2/3*(qx-current[0]),current[1]+2/3*(qy-current[1])]
                        c2 = [x+2/3*(qx-x),y+2/3*(qy-y)]
                        commands.append(' '.join(number(v) for v in [*c1,*c2,x,y])+' c')
                        current = [x,y]
                    else: commands.append('h')
                commands.append('S Q')
                counters['shapes'] += 1
            elif kind == 3:
                item = shape_profile(raw,obj)
                asset = media[item['image_id']]
                name = NameObject(f'/Image{len(images)}')
                images[name] = image_object(writer, files['media/'+asset])
                left,top,right,bottom = item['bounds']
                commands.append(f'q {number(right-left)} 0 0 {number(top-bottom)} {number(left)} {number(bottom)} cm {name} Do Q')
                counters['images'] += 1
            else:
                raise Unsupported(f'object type {kind} is unsupported')
    commands.append('Q')
    out[NameObject('/Resources')] = DictionaryObject({NameObject('/ExtGState'): states, NameObject('/XObject'): images})
    out[NameObject('/Contents')] = stream_object('\n'.join(commands).encode('ascii'))
    return out


def convert(source, destination, width_scale=0.5):
    if destination.exists(): raise FileExistsError(f'output already exists: {destination}')
    if source.is_dir() and destination.resolve().is_relative_to(source.resolve()):
        raise ValueError('output must be outside the input folder')
    files = load_input(source)
    order, bindings = sidecars(files)
    if not order: raise ValueError('note has no pages')
    expected = {item['id']+'.page' for item in order}
    if len(expected) != len(order) or expected != {n for n in files if n.endswith('.page')}:
        raise Unsupported('missing, duplicate, or unlisted note pages')
    metadata = note_header(files['note.note'])
    title = text_object(files['note.note'], metadata['title_object'])
    if text_object(files['note.note'], metadata['body_object']).strip():
        raise Unsupported('nonblank note body text is not yet supported')
    media = {entry['id']: entry['filename'] for entry in bindings}
    writer, readers, counters = PdfWriter(), {}, Counter()
    for index, item in enumerate(order, 1):
        try:
            raw = files[item['id']+'.page']
            pg = page(raw)
            props = pg['properties']
            if pg['issues'] or props['unconsumed_hex'] or pg['section_tail_hex']:
                raise Unsupported(f'undecoded page data: {pg["issues"]}')
            if pg['orientation'] or pg['offset_x'] or pg['offset_y'] or pg['text_only']:
                raise Unsupported('nondefault note orientation/offset/text-only mode')
            if props.get('background_image') or props.get('background_rotation') or props.get('template_uri') or props.get('custom_objects'):
                raise Unsupported('background image/template/custom object not supported')
            width, height = pg['width'], pg['height']
            if not width or not height: raise ValueError('invalid page dimensions')
            dest = writer.add_blank_page(width=width,height=height)
            color = props.get('background_color',0xffffffff)
            rgb = ' '.join(number((color >> shift & 255)/255) for shift in (16,8,0))
            dest[NameObject('/Contents')] = writer._add_object(stream_object(f'q {rgb} rg 0 0 {width} {height} re f Q'.encode('ascii')))
            for background in props.get('pdf',[]):
                asset = 'media/'+media[background['file_id']]
                if asset not in readers: readers[asset] = PdfReader(BytesIO(files[asset]))
                reader = readers[asset]
                page_index = background['page_index']
                if not 0 <= page_index < len(reader.pages): raise ValueError('PDF page index out of bounds')
                src = reader.pages[page_index]
                if src.rotation: src.transfer_rotation_to_content()
                if any(a.get_object().get('/Subtype') != '/Link' for a in src.get('/Annots', [])):
                    raise Unsupported('non-link PDF annotations are not supported')
                left,top,right,bottom = background['rect']
                box = src.cropbox
                if right <= left or bottom <= top or box.width <= 0 or box.height <= 0:
                    raise ValueError('invalid PDF placement rectangle')
                sx,sy = (right-left)/float(box.width),(bottom-top)/float(box.height)
                matrix = Transformation((sx,0,0,sy,left-float(box.left)*sx,height-bottom-float(box.bottom)*sy))
                dest.merge_transformed_page(src,matrix)
                counters['pdf_backgrounds'] += 1
            dest.merge_page(overlay(writer,pg,raw,media,files,width_scale,counters))
            dest.compress_content_streams()
            counters['pages'] += 1
        except (ValueError,KeyError,struct.error) as error:
            raise Unsupported(f'note page {index} ({item["id"]}): {error}') from error
    writer.add_metadata({'/Title': title, '/Creator': 'SNo2md own SDOCX decoder + pypdf'})
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Complete the file before publishing; link refuses an existing destination atomically.
    fd, temporary = tempfile.mkstemp(prefix='.sno2md-',suffix='.pdf',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            writer.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary,destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
        writer.close()
    return dict(counters)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,help='one .sdocx or unpacked note root')
    parser.add_argument('output',type=Path,help='new .pdf path; never overwritten')
    parser.add_argument('--width-scale',type=float,default=0.5, help='Stroke and shape width multiplier (default: 0.5)')
    args = parser.parse_args()
    if not math.isfinite(args.width_scale) or args.width_scale <= 0:
        parser.error('--width-scale must be finite and positive')
    try:
        counts = convert(args.source,args.output,args.width_scale)
    except (ValueError,KeyError,OSError,struct.error) as error:
        parser.exit(1,f'Conversion failed: {error}\nNo output was replaced.\n')
    print(f'{args.output}: {counts}')


if __name__ == '__main__':
    main()
