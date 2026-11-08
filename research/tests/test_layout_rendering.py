import unittest

from sno2md.layout_classifier import classify_page, document_profile
from sno2md.canvas_lines import canvas_line_plans, definition_plan
from sno2md.layout_ir import page_record
from sno2md.layout_renderer import render_page
from sno2md.visual_grouping import group_page


def page(*rows, frame=(0, 0, 720, 540)):
    raw, boxes = '', []
    for i, (kind, bbox, text) in enumerate(rows):
        boxes.append(dict(index=i, bbox=bbox, pos=(len(raw), len(raw)+len(text)), **{'class': kind}))
        raw += text
    return page_record('source', 1, frame, dict(text=raw, page_boxes=boxes))


def restore(output, audit):
    for insertion in reversed(audit['insertions']):
        a, b = insertion['output_span']
        output = output[:a] + insertion.get('original', '') + output[b:]
    return output


class LayoutRenderingTest(unittest.TestCase):
    def test_canvas_recovers_a_paragraph_gap_without_colons(self):
        from types import SimpleNamespace
        rows = ['The first paragraph ends here.', 'A second printed row', 'Third row already separate']
        lines = [dict(dir=(1, 0), bbox=(20, y-12, 300, y),
                      spans=[dict(origin=(20, y), size=16, text=text)])
                 for y,text in zip((70, 105, 125), rows)]
        pdf = SimpleNamespace(TEXTFLAGS_DICT=1, TEXT_PRESERVE_IMAGES=0)
        pdf_page = SimpleNamespace(rotation=0, get_text=lambda *args, **kwargs: dict(blocks=[dict(lines=lines)]))
        raw = rows[0]+' '+rows[1]+'  \n'+rows[2]+'\n\n'
        record = page(('text', (20, 50, 520, 300), raw))
        plans = canvas_line_plans(record, pdf, pdf_page)
        profile = classify_page(record)
        groups = group_page(record, profile)
        output, audit = render_page(record, profile, groups, 'canvas', raw, line_plans=plans)
        self.assertEqual(output, rows[0]+'\n\n '+rows[1]+'  \n'+rows[2]+'\n\n')
        self.assertEqual(restore(output, audit), raw)
        self.assertEqual(render_page(record, profile, groups, 'flow', raw, line_plans=plans)[0], raw)
        lines[1]['spans'][0]['origin'] = (20, 90)
        lines[2]['spans'][0]['origin'] = (20, 110)
        self.assertEqual(canvas_line_plans(record, pdf, pdf_page), {})
        repeated = page(('text', (20, 50, 520, 300), raw+'\n'+raw))
        lines[1]['spans'][0]['origin'] = (20, 105)
        lines[2]['spans'][0]['origin'] = (20, 125)
        self.assertEqual(canvas_line_plans(repeated, pdf, pdf_page), {})

    def test_canvas_keeps_wide_line_spacing_inside_a_paragraph(self):
        from types import SimpleNamespace
        rows = ['Frequency dividers are commonly used in PLLs, and',
                'frequency synthesizer.', 'This next sentence begins on a wrapped line.']
        lines = [dict(dir=(1, 0), bbox=(76, y-22, 605, y),
                      spans=[dict(origin=(76, y), size=22, text=text)])
                 for y, text in zip((165, 205, 245), rows)]
        pdf = SimpleNamespace(TEXTFLAGS_DICT=1, TEXT_PRESERVE_IMAGES=0)
        pdf_page = SimpleNamespace(rotation=0, get_text=lambda *args, **kwargs: dict(blocks=[dict(lines=lines)]))
        record = page(('text', (76, 130, 640, 250), ' '.join(rows)+'\n\n'))
        self.assertEqual(canvas_line_plans(record, pdf, pdf_page), {})

    def test_canvas_preserves_separate_aligned_labels_and_equation_rows(self):
        from types import SimpleNamespace
        examples = [
            ('Efficiency: The DC-to-RF conversion efficiency',
             'Power output variation: The output power level', 43),
            ('f1 = 2.165 GHz        IM1 = 2.175 GHz',
             'f2 = 2.155 GHz        IM2 = 2.145 GHz', 29),
        ]
        pdf = SimpleNamespace(TEXTFLAGS_DICT=1, TEXT_PRESERVE_IMAGES=0)
        for first, second, step in examples:
            with self.subTest(first=first):
                lines = [dict(dir=(1, 0), bbox=(20, y-16, 460, y),
                              spans=[dict(origin=(20, y), size=16, text=text)])
                         for y,text in zip((70, 70+step), (first, second))]
                blocks = [dict(lines=[line]) for line in lines]
                pdf_page = SimpleNamespace(rotation=0, get_text=lambda *args, **kwargs: dict(blocks=blocks))
                raw = first+' '+second+'\n\n'
                record = page(('text', (20, 50, 520, 300), raw))
                profile = classify_page(record)
                groups = group_page(record, profile)
                plans = canvas_line_plans(record, pdf, pdf_page)
                output, audit = render_page(record, profile, groups, 'canvas', raw, line_plans=plans)
                self.assertEqual(output, first+'  \n '+second+'\n\n')
                self.assertEqual(restore(output, audit), raw)
                self.assertEqual(audit['groups'][0]['applied'], 'visual_row_breaks')
                blocks[:] = [dict(lines=lines)]
                self.assertEqual(canvas_line_plans(record, pdf, pdf_page), {})

    def test_canvas_definition_lines_preserve_content_and_recover_visual_order(self):
        raw = ('This sentence runs across several words in the original first line '
               'and continues on the next line. y: second term x: first term 10<sup>2</sup> '
               'z: third term\n\n')
        record = page(('text', (20, 50, 520, 300), raw))
        region = record['regions'][0]
        rows = [(20, 60, 'This sentence runs across several words in the original first line'),
                (20, 80, 'and continues on the next line.'), (110, 160, 'x: first term 102'),
                (110, 185, 'y: second term'), (110, 210, 'z: third term')]
        lines = [dict(dir=(1, 0), bbox=(x, y-10, 500, y),
                      spans=[dict(origin=(x, y), size=16, text=text)]) for x,y,text in rows]
        plan = definition_plan(region, [dict(lines=lines)])
        self.assertIsNotNone(plan)
        profile = classify_page(record)
        groups = group_page(record, profile)
        plans = {region['id']: plan}
        output, audit = render_page(record, profile, groups, 'canvas', raw, line_plans=plans)
        self.assertIn('first line and continues', output)
        self.assertIn('&nbsp;x: first term 10<sup>2</sup>  \n', output)
        self.assertLess(output.index('x: first term'), output.index('y: second term'))
        self.assertEqual(restore(output, audit), raw)
        self.assertEqual(render_page(record, profile, groups, 'flow', raw, line_plans=plans)[0], raw)
        ambiguous = dict(region, text=raw.replace('z: third term', 'z: different term'))
        self.assertIsNone(definition_plan(ambiguous, [dict(lines=lines)]))

    def test_callout_after_object_replacement_and_review(self):
        record = page(
            ('text', (30, 60, 650, 80), 'Normal main paragraph.\n\n'),
            ('text', (100, 110, 330, 126), 'First definition.\n\n'),
            ('text', (100, 139, 322, 155), 'Second definition.\n\n'),
            ('text', (30, 180, 650, 198), 'The main text continues here.\n\n'),
            ('picture', (5, 215, 250, 340), '<!-- image -->\n![Native](native.png)\n\n'),
            ('text', (280, 230, 380, 280), 'This side text sits beside the figure.\n\n'),
        )
        profile = classify_page(record)
        self.assertEqual(profile['layout'], 'canvas')
        groups = group_page(record, profile)
        self.assertEqual([g['type'] for g in groups], ['plain', 'indented', 'plain', 'visual_group'])
        start, end = record['regions'][4]['pos']
        raw = record['raw_markdown']
        classic = raw[:start] + '\n\n' + raw[end:]
        output, audit = render_page(
            record, profile, groups, 'auto', classic,
            [dict(action='image', span=[start, end], output_span=[start, start+10])],
            [dict(span=[start, start+10], output_span=[start, start+2])],
        )
        self.assertIn('> [!note]\n> First definition.\n> \n> Second definition.', output)
        self.assertIn('> [!note]\n> This side text sits beside the figure.', output)
        self.assertEqual(restore(output, audit), classic)
        self.assertEqual(render_page(record, profile, groups, 'flow', classic)[0], classic)

    def test_nested_list_and_conflicting_positions(self):
        record = page(
            ('list-item', (20, 100, 500, 119), '- Parent bullet\n\n'),
            ('list-item', (48, 129, 510, 148), '- Child bullet\n\n'),
            ('list-item', (20, 163, 500, 182), '- Next parent\n\n'),
        )
        profile = classify_page(record)
        groups = group_page(record, profile)
        raw = record['raw_markdown']
        self.assertEqual(render_page(record, profile, groups, 'auto', raw)[0], raw)
        output, audit = render_page(record, profile, groups, 'canvas', raw)
        self.assertIn('\n    - Child bullet\n', output)
        self.assertEqual(restore(output, audit), raw)
        record['regions'][1]['pos'][0] = 0
        output, audit = render_page(record, profile, groups, 'canvas', raw)
        self.assertEqual(output, raw)
        self.assertEqual(audit['fallback'], 'conflicting_source_order_or_ranges')

    def test_columns_and_document_votes(self):
        text = 'A sustained paragraph in its own reading column. ' * 14 + '\n\n'
        record = page(
            ('text', (32, 80, 280, 210), text),
            ('text', (38, 220, 281, 390), text),
            ('text', (318, 80, 564, 210), text),
            ('text', (319, 220, 564, 390), text),
            frame=(0, 0, 600, 800),
        )
        profile = classify_page(record)
        self.assertEqual(profile['layout'], 'flow')
        self.assertIsNotNone(profile['features']['gutter_x'])
        groups = group_page(record, profile)
        self.assertTrue(all(group['type'] == 'plain' for group in groups))
        self.assertEqual(document_profile([profile], 1)['document_type'], 'paper')
        self.assertEqual(document_profile([profile], 5)['document_type'], 'unknown')

    def test_existing_markdown_list_indentation_is_preserved(self):
        record = page(
            ('list-item', (20, 100, 500, 119), '- Parent bullet\n\n'),
            ('list-item', (48, 129, 510, 148), '   - Already nested\n\n'),
            ('list-item', (20, 163, 500, 182), '- Next parent\n\n'),
        )
        profile = classify_page(record)
        groups = group_page(record, profile)
        raw = record['raw_markdown']
        output, audit = render_page(record, profile, groups, 'canvas', raw)
        self.assertEqual(output, raw)
        self.assertEqual(audit['groups'][0]['preserved'], 'native_list_indentation')
