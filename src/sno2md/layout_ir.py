"""In-memory projection of the experimental Layout IR onto one raw page."""
from math import isfinite


def mapping_status(pos, length):
    if pos is None:
        return 'missing'
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        start, stop = pos
        if type(start) is int and type(stop) is int and 0 <= start <= stop <= length:
            return 'empty' if start == stop else 'valid'
    return 'invalid'


def page_record(source_id, number, frame, chunk):
    """Offsets always address Python character slices of raw_markdown."""
    page_id = f'{source_id}:p{number}'
    markdown = chunk['text']
    regions = []
    for ordinal, box in enumerate(chunk.get('page_boxes', ())):
        pos = box.get('pos')
        status = mapping_status(pos, len(markdown))
        bbox = list(box['bbox'])
        if len(bbox) != 4 or not all(isfinite(value) for value in bbox):
            raise ValueError('invalid Layout box geometry')
        regions.append(dict(
            id=f'{page_id}:r{ordinal}', ordinal=ordinal, index=box.get('index'),
            bbox=bbox, pos=list(pos) if status in {'valid', 'empty'} else None,
            pos_status=status, **{'class': box.get('class')},
            text=markdown[slice(*pos)] if status in {'valid', 'empty'} else None,
        ))
    return dict(id=page_id, page=number, bbox=list(frame), raw_markdown=markdown, regions=regions)
