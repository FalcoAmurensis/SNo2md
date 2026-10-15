"""PDF preparation and rendering; no SDOCX binary decoding here."""
from collections import Counter
from io import BytesIO
import math
from pypdf import PdfReader, PdfWriter, PageObject, Transformation
from pypdf.generic import DictionaryObject, NameObject, NumberObject, FloatObject, DecodedStreamObject
from .decoder import png_pixels
from .model import Unsupported, RenderError


def number(value):
    if not math.isfinite(value):
        raise Unsupported('non-finite geometry')
    return f'{value:.6f}'.rstrip('0').rstrip('.') or '0'


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


def overlay(writer, pg, files, width_scale, counters):
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
                s = obj['decoded']
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
                shape = obj['decoded']
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
                item = obj['decoded']
                asset = item['asset']
                name = NameObject(f'/Image{len(images)}')
                images[name] = image_object(writer, files[asset])
                left,top,right,bottom = item['bounds']
                commands.append(f'q {number(right-left)} 0 0 {number(top-bottom)} {number(left)} {number(bottom)} cm {name} Do Q')
                counters['images'] += 1
            else:
                raise Unsupported(f'object type {kind} is unsupported')
    commands.append('Q')
    out[NameObject('/Resources')] = DictionaryObject({NameObject('/ExtGState'): states, NameObject('/XObject'): images})
    out[NameObject('/Contents')] = stream_object('\n'.join(commands).encode('ascii'))
    return out



def prepare_backgrounds(note, extract_text=False):
    """Preserve original PDF coordinates/text before normalizing page rotation."""
    readers, cache, pages = {}, {}, []
    try:
        for pg in note.pages:
            backgrounds = []
            for ref in pg['properties'].get('pdf', []):
                asset, index = ref['asset'], ref['page_index']
                if asset not in readers: readers[asset] = PdfReader(BytesIO(note.files[asset]))
                reader = readers[asset]
                if not 0 <= index < len(reader.pages): raise RenderError('PDF page index out of bounds')
                key = (asset, index)
                if key not in cache:
                    src = reader.pages[index]
                    rotation = src.rotation
                    crop = [float(v) for v in src.cropbox]
                    media = [float(v) for v in src.mediabox]
                    text = src.extract_text() if extract_text else None
                    if extract_text and not isinstance(text, str): raise RenderError('PDF text extraction returned no result')
                    # PDF /Rotate applies clockwise. Translate the rotated media bounds
                    # to the normalized origin, matching transfer_rotation_to_content().
                    rot = Transformation().rotate(-rotation)
                    corners = [rot.apply_on((x,y)) for x in (media[0],media[2]) for y in (media[1],media[3])]
                    normalize = rot.translate(-min(p[0] for p in corners), -min(p[1] for p in corners)) if rotation else Transformation()
                    if rotation: src.transfer_rotation_to_content()
                    if any(a.get_object().get('/Subtype') != '/Link' for a in src.get('/Annots', [])):
                        raise Unsupported('non-link PDF annotations are not supported')
                    cache[key] = (src, rotation, crop, media, normalize, text)
                src, rotation, crop, media, normalize, text = cache[key]
                left,top,right,bottom = ref['rect']
                box = src.cropbox
                if right <= left or bottom <= top or box.width <= 0 or box.height <= 0:
                    raise RenderError('invalid PDF placement rectangle')
                sx,sy = (right-left)/float(box.width),(bottom-top)/float(box.height)
                placement = Transformation((sx,0,0,sy,left-float(box.left)*sx,pg['height']-bottom-float(box.bottom)*sy))
                # Compose from original PDF bottom-left space to note top-left space.
                def mapped(x,y):
                    u,v = placement.apply_on(normalize.apply_on((x,y)))
                    return u, pg['height']-v
                origin,xunit,yunit = mapped(0,0),mapped(1,0),mapped(0,1)
                matrix = [xunit[0]-origin[0], xunit[1]-origin[1], yunit[0]-origin[0], yunit[1]-origin[1], *origin]
                info = {'asset': asset, 'pdf_page_number': index+1, 'note_rect': ref['rect'],
                        'original_cropbox': crop, 'original_mediabox': media, 'original_rotation': rotation,
                        'normalized_cropbox': [float(v) for v in box],
                        'original_pdf_to_note': matrix, 'text': text}
                backgrounds.append((src, placement, info))
            pages.append(backgrounds)
        return pages
    except (Unsupported, RenderError):
        raise
    except Exception as error:
        raise RenderError(f'PDF preparation/text extraction failed: {error}') from error


def write_pdf(note, destination, options, backgrounds):
    writer, counters = PdfWriter(), Counter()
    try:
        for pg, refs in zip(note.pages, backgrounds):
            width, height = pg['width'], pg['height']
            dest = writer.add_blank_page(width=width, height=height)
            color = pg['properties'].get('background_color',0xffffffff)
            rgb = ' '.join(number((color >> shift & 255)/255) for shift in (16,8,0))
            dest[NameObject('/Contents')] = writer._add_object(stream_object(f'q {rgb} rg 0 0 {width} {height} re f Q'.encode('ascii')))
            for src, matrix, info in refs:
                dest.merge_transformed_page(src, matrix)
                counters['pdf_backgrounds'] += 1
            dest.merge_page(overlay(writer, pg, note.files, options.width_scale, counters))
            dest.compress_content_streams()
            counters['pages'] += 1
        writer.add_metadata({'/Title': note.title, '/Creator': 'SNo2md own SDOCX decoder + pypdf'})
        with open(destination,'wb') as stream:
            writer.write(stream)
        return dict(counters)
    except OSError:
        raise
    except Exception as error:
        raise RenderError(f'PDF rendering failed: {error}') from error
    finally:
        writer.close()
