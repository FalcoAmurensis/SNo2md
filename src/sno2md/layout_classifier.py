"""Conservative page rules. Document votes never override page decisions."""
from collections import Counter
from statistics import median

BODY = {'text', 'list-item'}


def column_gutter(regions, width, height):
    body = [r['bbox'] for r in regions if r['class'] in BODY
            and r['bbox'][2] - r['bbox'][0] >= .15 * width]
    for fraction in (.50, .48, .52, .46, .54, .44, .56, .42, .58):
        split = width * fraction
        left = [b for b in body if b[2] <= split - .005 * width]
        right = [b for b in body if b[0] >= split + .005 * width]
        if min(len(left), len(right)) < 2 or len(left) + len(right) < .65 * len(body):
            continue
        overlap = min(max(b[3] for b in side) for side in (left, right)) - max(
            min(b[1] for b in side) for side in (left, right))
        if overlap >= .15 * height:
            return split
    return None


def classify_page(page):
    x0, y0, x1, y1 = page['bbox']
    width, height = x1 - x0, y1 - y0
    if min(width, height) <= 0:
        raise ValueError('invalid page size')
    regions = page['regions']
    body = [r for r in regions if r['class'] in BODY and r['text']]
    counts = [len(r['text'].split()) for r in body]
    words = sum(counts)
    coverage = min(1, sum(max(0, r['bbox'][2]-r['bbox'][0]) *
                          max(0, r['bbox'][3]-r['bbox'][1]) for r in body) / (width*height))
    longest = max(counts, default=0)
    typical = median(counts) if counts else 0
    gutter = column_gutter(body, width, height)
    landscape = width / height >= 1.18
    gap = max((b['bbox'][1] - a['bbox'][3] for a, b in zip(body, body[1:])), default=0)
    objects = sum(r['class'] in {'picture', 'table', 'formula'} for r in regions)
    headings = sum(r['class'] in {'section-header', 'title'} for r in regions)
    evidence = []
    if gutter is not None:
        evidence.append('vertically_overlapping_columns')
    if landscape:
        evidence.append('landscape')
    dense = words >= 175 and longest >= 45
    spatial = gap >= .035 * height or objects > 0 or (
        len(body) >= 3 and headings > 0 and typical <= 22)
    if dense and (gutter is not None or not landscape or coverage >= .42):
        layout, vote = 'flow', 'paper'
        evidence.append('sustained_prose')
    elif (landscape and coverage <= .54 and spatial
          and (12 <= words <= 240 or (6 <= words < 12 and objects and headings))):
        layout, vote = ('mixed' if longest >= 55 else 'canvas'), 'slide'
        evidence.append('sparse_spatial_blocks')
    elif words >= 90 and longest >= 45 and not landscape:
        layout, vote = 'flow', 'unknown'
        evidence.append('continuous_prose')
    else:
        layout = vote = 'unknown'
        evidence.append('insufficient_combined_evidence')
    return dict(page=page['page'], layout=layout, vote=vote, evidence=evidence, features=dict(
        aspect_ratio=width/height, words=words, words_per_10000_pt2=words*10000/(width*height),
        body_regions=len(body), text_box_coverage=coverage, longest_block_words=longest,
        median_block_words=typical, gutter_x=gutter, max_body_gap=gap, objects=objects, headings=headings,
    ))


def document_profile(profiles, total_pages):
    votes = Counter(profile['vote'] for profile in profiles)
    decisive = votes['paper'] + votes['slide']
    winner = max(('paper', 'slide'), key=votes.__getitem__)
    if decisive == 0 or decisive * 2 < total_pages or votes[winner] * 3 < decisive * 2:
        winner = 'unknown'
    return dict(document_type=winner, pages_analyzed=len(profiles), total_pages=total_pages,
                votes={kind: votes[kind] for kind in ('paper', 'slide', 'unknown')})
