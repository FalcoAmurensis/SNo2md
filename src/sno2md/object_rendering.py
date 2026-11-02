"""One visual representation per source region, shared by pages and chunks.

Only provenance-addressed spans are replaced. Ambiguous text and overlapping
spans stay native and are reported; no document-wide text or image-hash deletion.
"""
import base64
import hashlib
import re

VISUAL_KINDS = {'picture', 'formula', 'table'}


def same_region(a, b, tolerance=0.5):
    """Near-identical source geometry, not merely identical picture contents."""
    if a['page'] != b['page']:
        return False
    if not all(abs(x-y) <= tolerance for x,y in zip(a['bbox'], b['bbox'])):
        return False
    x0,y0,x1,y1 = a['bbox']; u0,v0,u1,v1 = b['bbox']
    intersection = max(0,min(x1,u1)-max(x0,u0))*max(0,min(y1,v1)-max(y0,v0))
    area = max((x1-x0)*(y1-y0),(u1-u0)*(v1-v0))
    return area > 0 and intersection/area >= .98


class ObjectRenderer:
    """Create one instance per source document and reuse it across its chunks."""
    def __init__(self, document, source_link=None, dpi=150):
        self.document, self.source_link, self.dpi = document, source_link, dpi
        self.emitted = []

    def render(self, region):
        import pymupdf as pdf
        page_number = region['page']
        if not 1 <= page_number <= len(self.document):
            raise ValueError('invalid source page')
        bbox = pdf.Rect(region['bbox'])
        if bbox.is_empty or bbox.is_infinite:
            raise ValueError('invalid source region')
        page = self.document[page_number-1]
        clip = bbox & page.rect
        if clip.is_empty:
            raise ValueError('source region is outside the page')
        image = page.get_pixmap(clip=clip, dpi=self.dpi, alpha=False).tobytes('png')
        uri = 'data:image/png;base64,'+base64.b64encode(image).decode('ascii')
        label = {'table':'Table','formula':'Formula','picture':'Figure'}[region['kind']]
        value = f'![{label} · page {page_number}]({uri})'
        if self.source_link:
            value = f'[{value}]({self.source_link}#page={page_number})'
        return '\n\n'+value+'\n\n', hashlib.sha256(image).hexdigest()


def verify_surrounding_text(original, changed, records):
    """Verify every gap independently using original and output coordinates."""
    old_cursor = new_cursor = 0
    for r in records:
        if r['action'] not in {'image','duplicate_omitted'}:
            continue
        start,end = r['span']; new_start,new_end = r['output_span']
        if original[old_cursor:start] != changed[new_cursor:new_start]:
            raise ValueError('text outside source-object spans changed')
        old_cursor,new_cursor = end,new_end
    if original[old_cursor:] != changed[new_cursor:]:
        raise ValueError('text outside source-object spans changed')


def _replace(text, regions, renderer, unresolved=()):
    records = list(unresolved); valid = []
    for region in regions:
        start,end = region['span']
        if not 0 <= start < end <= len(text):
            records.append(dict(**region, action='unresolved', reason='invalid or empty native span'))
        else:
            # Multiple metadata IDs may address the same exact source span.
            alias = next((r for r in valid if r['span']==region['span'] and same_region(r,region)),None)
            if alias:
                records.append(dict(**region, action='alias', canonical_key=alias['key']))
            else:
                valid.append(region)
    conflicts = set()
    for i,a in enumerate(valid):
        for j,b in enumerate(valid[:i]):
            if max(a['span'][0],b['span'][0]) < min(a['span'][1],b['span'][1]):
                conflicts.update([i,j])
    pieces=[];cursor=0;length=0;accepted=[]
    for i,r in sorted(enumerate(valid),key=lambda pair:pair[1]['span']):
        if i in conflicts:
            records.append(dict(**r,action='unresolved',reason='overlapping native spans; retain native text'))
            continue
        previous = next((p for p in renderer.emitted if same_region(p,r)),None)
        if previous:
            value='';action='duplicate_omitted';digest=previous.get('image_sha256')
        else:
            try:
                value,digest = renderer.render(r)
            except Exception as error:
                records.append(dict(**r,action='unresolved',reason=f'image rendering failed: {error}'))
                continue
            action='image'
        start,end=r['span'];gap=text[cursor:start]
        pieces.extend([gap,value]);length+=len(gap)
        record=dict(**r,action=action,image_sha256=digest,output_span=[length,length+len(value)])
        if previous:record['canonical_key']=previous['key']
        else:renderer.emitted.append(record)
        accepted.append(record);length+=len(value);cursor=end
    pieces.append(text[cursor:]);changed=''.join(pieces)
    verify_surrounding_text(text,changed,accepted)
    return changed,accepted+records


def render_page_objects(page_result, renderer):
    """Use native page_boxes.pos; do not search for formula text globally."""
    page=page_result['metadata']['page_number'];regions=[];unresolved=[]
    for box in page_result.get('page_boxes',[]):
        kind=box.get('class')
        if kind not in VISUAL_KINDS:continue
        region=dict(key=f'p{page}.b{box["index"]}',page=page,bbox=list(box['bbox']),kind=kind)
        if not box.get('pos'):
            unresolved.append(dict(**region,action='unresolved',reason='native page span unavailable'))
        else:regions.append(dict(**region,span=list(box['pos'])))
    return _replace(page_result['text'],regions,renderer,unresolved)


def render_chunk_objects(chunk, elements, renderer):
    """Replace only an exact unique object text inside its addressed chunk."""
    regions=[];unresolved=[];seen=set();text=chunk['text']
    for key in chunk['metadata']['element_ids']:
        if key in seen:continue
        seen.add(key);element=elements[key];kind=element['boxclass']
        if kind not in VISUAL_KINDS:continue
        region=dict(key=key,page=element['page'],bbox=list(element['bbox']),kind=kind)
        canonical=element['text']
        matches=list(re.finditer(re.escape(canonical),text)) if canonical else []
        if len(matches)!=1:
            unresolved.append(dict(**region,action='unresolved',reason='object text absent or ambiguous; retain native text'))
        else:regions.append(dict(**region,span=[matches[0].start(),matches[0].end()]))
    native_images=list(re.finditer(r'!\[[^\]\n]*\]\([^\n)]+\)',text))
    if any(not any(r['span'][0]<=m.start() and m.end()<=r['span'][1] for r in regions)
           for m in native_images):
        safe=[]
        for r in regions:
            if any(r['span'][0]<=m.start() and m.end()<=r['span'][1] for m in native_images):
                safe.append(r)
            else:
                unresolved.append(dict(**r,action='unresolved',
                    reason='existing native image has ambiguous source ownership; retain native text'))
        regions=safe
    return _replace(text,regions,renderer,unresolved)
