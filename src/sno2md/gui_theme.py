"""Sun Valley base plus SNo2md's flat elements; presentation only, never sync settings."""
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import ttk

import sv_ttk


# One accent per mode; semantic colors (yellow/red) are reserved for problems.
PALETTES = {
    'light': dict(bg='#fafafa', fg='#1c1c1c', sub='#5f5f5f', muted='#707070', table='#ffffff',
                  status='#f3f3f3', selection='#e8f1fb', accent='#005fb8', accent_hover='#196ebf',
                  accent_pressed='#4c8ccb', on_accent='#ffffff', border='#e3e3e3', line='#eeeeee',
                  control='#ffffff', control_hover='#f6f6f6', control_pressed='#f0f0f0',
                  disabled_bg='#f5f5f5', disabled_fg='#a0a0a0',
                  green='#39764c', yellow='#8a5300', red='#c42b1c'),
    'dark': dict(bg='#1c1c1c', fg='#f5f5f5', sub='#c2c2c2', muted='#949494', table='#262626',
                 status='#202020', selection='#1e3a4f', accent='#57c8ff', accent_hover='#4fb5e6',
                 accent_pressed='#47a1cc', on_accent='#08202e', border='#3a3a3a', line='#303030',
                 control='#2d2d2d', control_hover='#323232', control_pressed='#282828',
                 disabled_bg='#262626', disabled_fg='#6e6e6e',
                 green='#87cf9b', yellow='#f2c94c', red='#ff99a4'),
}
# Logical spacing steps; every use goes through Theme.pixels() so DPI follows.
SPACE = dict(xs=4, s=8, m=12, l=16, xl=24)
RADIUS = 2


class Theme:
    def __init__(self, root):
        self.root = root
        from .gui_icons import Icons
        self.icons = Icons(root)
        self.images = {}
        self.sprite_scale = 1.0
        self.mode = tk.StringVar(root, 'light')
        self.apply('light')
        root.bind_class('Toplevel', '<Map>', self.mapped, add=True)
        root.bind('<Map>', self.mapped, add=True)

    @property
    def colors(self):
        return PALETTES[self.mode.get()]

    @property
    def scale(self):
        return float(self.root.tk.call('tk', 'scaling'))/(96/72)

    def pixels(self, value):
        return max(1, round(value*self.scale))

    def space(self, name):
        return self.pixels(SPACE[name])

    def apply(self, mode):
        if mode not in PALETTES:
            raise ValueError(mode)
        self.mode.set(mode)
        sv_ttk.set_theme(mode, root=self.root._root())
        # Leave theme/geometry events to the normal Tk loop. Flushing them here
        # would re-enter layout callbacks while a theme is only partly configured.
        c = self.colors
        style = ttk.Style(self.root)
        # Point fonts follow Tk's DPI scaling, including Korean fallback.
        for name, size, weight in (('SunValleyBodyFont', 10, 'normal'),
                ('SunValleyCaptionFont', 9, 'normal'), ('SunValleyBodyStrongFont', 10, 'bold'),
                ('SunValleySubtitleFont', 15, 'bold')):
            self.root.tk.call('font', 'configure', name, '-family', '맑은 고딕', '-size', size, '-weight', weight)
        self.sprites()
        prefix = self.elements(style)
        self.layouts(style, prefix)
        p, sp = self.pixels, self.space
        style.configure('.', background=c['bg'], foreground=c['fg'])
        style.configure('TLabel', background=c['bg'], foreground=c['fg'])
        style.configure('Muted.TLabel', foreground=c['sub'])
        style.configure('Section.TLabel', font='SunValleyBodyStrongFont')
        style.configure('Title.TLabel', font='SunValleySubtitleFont')
        style.configure('Card.TFrame', background=c['table'])
        style.configure('Card.TLabel', background=c['table'], foreground=c['fg'])
        style.configure('CardMuted.TLabel', background=c['table'], foreground=c['sub'], font='SunValleyCaptionFont')
        style.configure('Card.Switch.TCheckbutton', background=c['table'])
        style.configure('Status.TFrame', background=c['status'])
        style.configure('Status.TLabel', background=c['status'], foreground=c['sub'])
        style.configure('StatusStrong.TLabel', background=c['status'], foreground=c['fg'])
        style.configure('Empty.TLabel', background=c['table'], foreground=c['sub'])
        style.configure('TButton', padding=(sp('m'), p(5)), foreground=c['fg'], anchor='center')
        style.map('TButton', foreground=[('disabled', c['disabled_fg'])])
        style.configure('Accent.TButton', foreground=c['on_accent'])
        style.map('Accent.TButton', foreground=[('disabled', c['disabled_fg'])])
        style.configure('TMenubutton', padding=(sp('m'), p(5), p(10), p(5)), foreground=c['fg'])
        style.map('TMenubutton', foreground=[('disabled', c['disabled_fg'])])
        style.configure('Chip.TButton', padding=(p(10), p(5)))
        style.configure('Group.TButton', padding=(0, p(4), 0, p(10)), foreground=c['sub'], anchor='w')
        style.map('Group.TButton', foreground=[('selected', c['fg']), ('active', c['fg'])],
                  font=[('selected', 'SunValleyBodyStrongFont')])
        style.configure('Zero.Group.TButton', foreground=c['muted'])
        style.configure('TEntry', padding=(p(10), p(5)), foreground=c['fg'])
        style.configure('Search.TEntry', padding=(p(34), p(5), p(10), p(5)))
        style.configure('TCombobox', padding=(p(10), p(5), 0, p(5)), foreground=c['fg'])
        # Readonly comboboxes must not paint their text with a selection block.
        style.map('TCombobox', selectbackground=[('readonly', c['control'])],
                  selectforeground=[('readonly', c['fg'])],
                  fieldbackground=[('readonly', c['control'])], foreground=[('disabled', c['disabled_fg'])])
        style.configure('Treeview', background=c['table'], fieldbackground=c['table'], foreground=c['fg'],
                        rowheight=self.pixels(30))
        style.map('Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['fg'])])
        style.configure('Document.Treeview', background=c['table'], fieldbackground=c['table'],
                        foreground=c['fg'], rowheight=self.pixels(36), font='SunValleyBodyFont')
        style.map('Document.Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['fg'])])
        style.configure('Document.Treeview.Heading', font='SunValleyCaptionFont', foreground=c['sub'],
                        padding=(p(10), p(7)))
        style.configure('Document.Treeview.Cell', padding=(p(10), 0))
        self.refresh(self.root)

    # -- images -----------------------------------------------------------

    def box(self, w, h, fill, border=None, radius=0, bottom=None, bottom_width=1, left=None,
            left_width=0, top=None, inset=0, bar=None):
        """Flat rectangle photo with optional hairline border, corners and edge rules.

        ``bar`` paints a vertical rounded bar (x0, x1, color) used for thin scrollbar thumbs.
        """
        photo = tk.PhotoImage(master=self.root, width=w, height=h)
        if fill: photo.put(fill, to=(0, 0, w, h))
        else: photo.blank()
        if bar:
            x0, x1, color = bar
            photo.put(color, to=(x0, radius, x1, h-radius))
            for y in range(radius):
                pad = 1 if y < radius-1 else 0
                photo.put(color, to=(x0+pad, y, x1-pad, y+1))
                photo.put(color, to=(x0+pad, h-1-y, x1-pad, h-y))
            if not fill:
                return self.keep(photo)
        if border:
            for rect in ((0, 0, w, 1), (0, h-1, w, h), (0, 0, 1, h), (w-1, 0, w, h)):
                photo.put(border, to=rect)
        if top: photo.put(top, to=(0, 0, w, 1))
        if bottom: photo.put(bottom, to=(0, h-bottom_width, w, h))
        if left: photo.put(left, to=(0, 0, left_width, h))
        if radius:
            # Tiny corners: outside pixels transparent, the arc in the border color.
            for cx, cy, dx, dy in ((0, 0, 1, 1), (w-1, 0, -1, 1), (0, h-1, 1, -1), (w-1, h-1, -1, -1)):
                for i in range(radius):
                    for j in range(radius):
                        distance = ((radius-0.5-i)**2+(radius-0.5-j)**2)**.5
                        x, y = cx+dx*i, cy+dy*j
                        if distance > radius:
                            photo.transparency_set(x, y, True)
                        elif distance > radius-1 and border:
                            photo.put(border, to=(x, y, x+1, y+1))
        return self.keep(photo)

    def keep(self, photo):
        self.images[str(photo)] = photo
        return photo

    def dot(self, kind):
        """Status dot: filled for a state, a ring while work is in progress."""
        c = self.colors
        color = {'idle': c['muted'], 'watch': c['accent'], 'sync': c['accent'],
                 'stopping': c['muted'], 'error': c['red'], 'setup': c['yellow']}[kind]
        key = ('dot', kind, self.mode.get(), self.pixels(10))
        if key not in self.images:
            size = self.pixels(10)
            photo = tk.PhotoImage(master=self.root, width=size, height=size)
            photo.blank()
            centre, outer = (size-1)/2, size/2
            inner = outer - max(2, self.pixels(2)) if kind in ('sync', 'stopping') else -1
            for y in range(size):
                for x in range(size):
                    distance = ((x-centre)**2+(y-centre)**2)**.5
                    if inner < distance <= outer-.2:
                        photo.put(color, to=(x, y, x+1, y+1))
            self.images[key] = photo
        return self.images[key]

    def chevron(self, color):
        key = ('chevron', color, self.pixels(12))
        if key not in self.images:
            self.images[key] = self.icons.get('chevron_down', color, self.pixels(12))
        return self.images[key]

    def padded_icon(self, name, color, width, size):
        """Centre an icon in a wider transparent photo (Combobox arrow element)."""
        icon = self.icons.get(name, color, size)
        photo = tk.PhotoImage(master=self.root, width=width, height=size)
        photo.blank()
        self.root.tk.call(photo, 'copy', icon, '-to', (width-size)//2, 0)
        return self.keep(photo)

    def sprites(self):
        """Resample Sun Valley's 100% sprites in place for the current DPI scale.

        Its theme file draws checkboxes, switches and arrows from fixed-size images;
        replacing the photo contents keeps every existing element and state map.
        """
        scale = round(self.scale, 2)
        if scale == self.sprite_scale: return
        import pymupdf
        base = Path(sv_ttk.__file__).with_name('theme')
        for mode in ('light', 'dark'):
            array = f'ttk::theme::sv_{mode}::I'
            if not int(self.root.tk.call('array', 'exists', array)): continue
            # Resample the whole sheet once (smooth), then crop each sprite with Tk.
            sheet = pymupdf.Pixmap(str(base/f'spritesheet_{mode}.png'))
            if scale != 1:
                sheet = pymupdf.Pixmap(sheet, round(sheet.width*scale), round(sheet.height*scale))
            source = tk.PhotoImage(master=self.root, data=sheet.tobytes('png'))
            text = (base/f'sprites_{mode}.tcl').read_text(encoding='utf-8')
            for name, x, y, w, h in re.findall(r'^\s*([\w-]+) (\d+) (\d+) (\d+) (\d+)', text, re.M):
                x0, y0 = round(int(x)*scale), round(int(y)*scale)
                x1, y1 = round((int(x)+int(w))*scale), round((int(y)+int(h))*scale)
                target = self.root.tk.call('set', f'{array}({name})')
                self.root.tk.call(target, 'blank')
                self.root.tk.call(target, 'configure', '-width', x1-x0, '-height', y1-y0)
                self.root.tk.call(target, 'copy', source, '-from', x0, y0, x1, y1)
        self.sprite_scale = scale

    # -- elements and layouts ---------------------------------------------

    def elements(self, style):
        """Create this mode/scale's image elements once; returns their name prefix."""
        c, p = self.colors, self.pixels
        prefix = f'SN{self.mode.get()}{round(self.scale*100)}'
        if f'{prefix}.button' in style.element_names(): return prefix
        r, b = p(RADIUS), p(RADIUS)+1

        def element(name, images, border, **options):
            normal, *states = images
            style.element_create(f'{prefix}.{name}', 'image', normal, *states, border=border,
                                 sticky=options.get('sticky', 'nswe'))

        def button(fill, hover, pressed, focus_border, disabled_fill, border=c['border'], selected=None):
            size = 40
            images = [self.box(size, size, fill, border, r)]
            images.append(('disabled', self.box(size, size, disabled_fill, c['border'], r)))
            if selected: images.append(('selected', self.box(size, size, *selected, r)))
            images += [('pressed', self.box(size, size, pressed, border, r)),
                       (('focus', 'active'), self.box(size, size, hover, focus_border, r)),
                       ('active', self.box(size, size, hover, border, r)),
                       ('focus', self.box(size, size, fill, focus_border, r))]
            return images

        element('button', button(c['control'], c['control_hover'], c['control_pressed'], c['accent'], c['disabled_bg']), b)
        element('accent', button(c['accent'], c['accent_hover'], c['accent_pressed'], c['fg'], c['disabled_bg'],
                                 border=c['accent']), b)
        element('chip', button(c['control'], c['control_hover'], c['control_pressed'], c['accent'], c['disabled_bg'],
                               selected=(c['selection'], c['accent'])), b)
        rule = p(3)
        element('tab', [self.box(64, 32, c['bg']),
                        ('selected', self.box(64, 32, c['bg'], bottom=c['accent'], bottom_width=rule)),
                        ('focus', self.box(64, 32, c['bg'], bottom=c['muted'], bottom_width=1))], (0, 0, 0, rule))
        field = dict(radius=r)
        element('field', [self.box(64, 32, c['control'], c['border'], bottom=c['muted'], **field),
                          ('disabled', self.box(64, 32, c['disabled_bg'], c['border'], **field)),
                          ('focus', self.box(64, 32, c['control'], c['border'], bottom=c['accent'], bottom_width=p(2), **field)),
                          ('hover', self.box(64, 32, c['control_hover'], c['border'], bottom=c['muted'], **field))],
                (b, b, b, max(b, p(2))))
        style.element_create(f'{prefix}.arrow', 'image',
                             self.padded_icon('chevron_down', c['sub'], p(30), p(12)),
                             ('disabled', self.padded_icon('chevron_down', c['disabled_fg'], p(30), p(12))), sticky='')
        element('card', [self.box(64, 64, c['table'], c['border'], r)], b)
        element('surface', [self.box(256, 128, c['table'], c['border'], r)], b)
        element('statusbar', [self.box(256, 32, c['status'], top=c['border'])], (0, 1, 0, 0))
        element('heading', [self.box(256, 32, c['table'], bottom=c['border'])], (0, 0, 0, 1))
        # Large flat tiles keep native drawing calls bounded when the list is maximized.
        element('row', [self.box(256, 128, c['table'], bottom=c['line']),
                        ('selected', self.box(256, 128, c['selection'], bottom=c['line']))],
                (0, 0, 0, 1))
        width, thin, wide = p(12), p(4), p(6)
        def thumb(size, color):
            x0 = (width-size)//2
            return self.box(width, 32, None, radius=p(RADIUS), bar=(x0, x0+size, color))
        self.indicators(prefix)
        style.element_create(f'{prefix}.trough', 'image', self.box(width, 32, None), border=0, sticky='ns')
        style.element_create(f'{prefix}.thumb', 'image', thumb(thin, c['muted']),
                             ('pressed', thumb(wide, c['sub'])), ('active', thumb(wide, c['sub'])),
                             border=(0, p(RADIUS)+1), sticky='nswe')
        return prefix

    def indicators(self, prefix):
        """Re-create Sun Valley's indicator elements with widths for this scale.

        Its theme fixes them in 100% pixels (switch 46, check/radio/tree 26), which
        would crop the resampled sprites. Same sprites and state maps, scaled width.
        """
        mode = self.mode.get()
        text = (Path(sv_ttk.__file__).with_name('theme')/f'{mode}.tcl').read_text(encoding='utf-8')
        array = f'ttk::theme::sv_{mode}::I'
        sprite = lambda match: self.root.tk.eval(f'set {array}({match.group(1)})')
        for source, target in (('Switch.indicator', 'switch'), ('Checkbutton.indicator', 'check'),
                               ('Radiobutton.indicator', 'radio'), ('Treeitem.indicator', 'treeitem')):
            found = re.search(re.escape(source) + r' image \\\s*\[list (.*?)\]\s*-width (\d+) -sticky (\S+)', text, re.S)
            if not found: continue
            spec = re.sub(r'\$I\(([\w-]+)\)', sprite, found.group(1).replace('\\\n', ' '))
            self.root.tk.eval(f'ttk::style element create {prefix}.{target} image [list {spec}] '
                              f'-width {self.pixels(int(found.group(2)))} -sticky {found.group(3)}')

    def layouts(self, style, prefix):
        def button(element, part='Button'):
            return [(f'{prefix}.{element}', {'sticky': 'nswe', 'children': [
                (f'{part}.padding', {'sticky': 'nswe', 'children': [
                    (f'{part}.label', {'sticky': 'nswe', 'expand': '1'})]})]})]
        style.layout('TButton', button('button'))
        style.layout('Accent.TButton', button('accent'))
        style.layout('Chip.TButton', button('chip'))
        style.layout('Group.TButton', button('tab'))
        style.layout('TMenubutton', button('button', 'Menubutton'))
        style.layout('TEntry', [(f'{prefix}.field', {'sticky': 'nswe', 'children': [
            ('Entry.padding', {'sticky': 'nswe', 'children': [('Entry.textarea', {'sticky': 'nswe'})]})]})])
        style.layout('TCombobox', [(f'{prefix}.field', {'sticky': 'nswe', 'children': [
            (f'{prefix}.arrow', {'side': 'right', 'sticky': 'ns'}),
            ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
        names = style.element_names()
        for layout, part, element in (('TCheckbutton', 'Checkbutton', 'check'), ('Switch.TCheckbutton', 'Switch', 'switch'),
                                      ('TRadiobutton', 'Radiobutton', 'radio')):
            if f'{prefix}.{element}' not in names: continue
            style.layout(layout, [(f'{part}.button', {'sticky': 'nswe', 'children': [
                (f'{part}.padding', {'sticky': 'nswe', 'children': [
                    (f'{prefix}.{element}', {'side': 'left', 'sticky': ''}),
                    (f'{part}.label', {'side': 'right', 'expand': '1', 'sticky': 'nswe'})]})]})])
        if f'{prefix}.treeitem' in names:
            style.layout('Item', [('Treeitem.padding', {'sticky': 'nswe', 'children': [
                (f'{prefix}.treeitem', {'side': 'left', 'sticky': ''}), ('Treeitem.image', {'side': 'left', 'sticky': ''}),
                ('Treeitem.text', {'sticky': 'nswe'})]})])
        style.layout('Card.TFrame', [(f'{prefix}.card', {'sticky': 'nswe'})])
        style.layout('Status.TFrame', [(f'{prefix}.statusbar', {'sticky': 'nswe'})])
        style.layout('Document.Treeview', [(f'{prefix}.surface', {'sticky': 'nswe', 'children': [
            ('Treeview.padding', {'sticky': 'nswe', 'children': [('Treeview.treearea', {'sticky': 'nswe'})]})]})])
        style.layout('Document.Treeview.Heading', [(f'{prefix}.heading', {'sticky': 'nswe', 'children': [
            ('Treeheading.padding', {'sticky': 'nswe', 'children': [
                ('Treeheading.image', {'side': 'right'}), ('Treeheading.text', {'sticky': 'we'})]})]})])
        style.layout('Document.Treeview.Row', [(f'{prefix}.row', {'sticky': 'nswe'})])
        style.layout('Thin.Vertical.TScrollbar', [(f'{prefix}.trough', {'sticky': 'ns', 'children': [
            (f'{prefix}.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        style.configure('Document.Treeview', borderwidth=1)

    # -- widgets ------------------------------------------------------------

    def button_icon(self, button, name, accent=False):
        c = self.colors
        color = c['on_accent'] if accent else c['fg']
        key = (name, color, self.pixels(16), c['disabled_fg'])
        if getattr(button, '_icon_key', None) == key: return
        button._icon_key = key
        normal = self.icons.get(name, color, self.pixels(16))
        disabled = self.icons.get(name, c['disabled_fg'], self.pixels(16))
        button.configure(image=(normal, 'disabled', disabled), compound='left')

    def menu_chevron(self, button):
        c = self.colors
        key = ('chevron', c['fg'], self.pixels(12))
        if getattr(button, '_icon_key', None) == key: return
        button._icon_key = key
        button.configure(image=(self.chevron(c['fg']), 'disabled', self.chevron(c['disabled_fg'])), compound='right')

    def chip_icon(self, button, name, color_key):
        c = self.colors
        key = (name, c[color_key], self.pixels(16))
        if getattr(button, '_icon_key', None) == key: return
        button._icon_key = key
        button.configure(image=self.icons.get(name, c[color_key], self.pixels(16)), compound='left')

    def main(self, app):
        self.button_icon(app.once_button, 'sync', True)
        self.button_icon(app.open_button, 'folder')
        self.button_icon(app.settings_button, 'settings')
        self.button_icon(app.details_button, 'info')
        for button, name in ((app.retry_button, 'sync'), (app.restore_button, 'folder'),
                             (app.resolve_button, 'warning'), (app.policy_button, 'sync')):
            self.button_icon(button, name)
        for button in (app.global_more, app.action_more):
            self.menu_chevron(button)
        self.chip_icon(app.attention_chip, 'warning', 'yellow')
        self.chip_icon(app.failure_chip, 'dismiss_circle', 'red')
        c = self.colors
        app.search_icon.configure(image=self.icons.get('search', c['muted'], self.pixels(16)))
        for label in (app.search_icon, app.search_placeholder):
            label.configure(background=c['control'], foreground=c['muted'])
        app.search_icon.place(x=self.pixels(10), rely=.5, anchor='w')
        app.search_placeholder.place(x=self.pixels(34), rely=.5, anchor='w')
        app.tree.tag_configure('attention', foreground=c['fg'])
        # Theme changes must not reset widths adjusted by the user. Only the
        # fixed checkbox and status icon columns follow DPI.
        app.tree.column('check', width=self.pixels(44), minwidth=self.pixels(40))
        app.tree.column('status', width=self.pixels(56), minwidth=self.pixels(48))
        if not getattr(app, '_columns_initialized', False):
            app.tree.column('name', width=self.pixels(640))
            app.tree.column('time', width=self.pixels(150))
            app._columns_initialized = True
        app.search_hint()
        # apply() recreates theme-local style options even when mode/scale is
        # unchanged, so restore the selected density after every explicit apply.
        app._theme_density = None
        self.density(app)
        if hasattr(app, 'status_cells'): app.status_cells.queue()
        if hasattr(app, 'list_scroll'): app.list_scroll.refresh()

    def density(self, app):
        # Normalize the measured fixed content back to regular spacing. This
        # makes the decision independent of the current density (no oscillation).
        p, sp = self.pixels, self.space
        regular = dict(top=sp('m'), command=sp('l'), tabs=sp('m'), table=p(10), tab=p(10), status=p(5))
        compact = dict(top=sp('s'), command=sp('s'), tabs=sp('s'), table=p(6), tab=p(5), status=p(2))
        rows = max(int(button.grid_info()['row']) for button in app.group_buttons.values())+1
        reduction = sum(regular[k]-compact[k] for k in ('top', 'command', 'tabs', 'table'))
        reduction += (regular['tab']-compact['tab'])*rows + 2*(regular['status']-compact['status'])
        regular_fixed = app.fixed_height() + (reduction if getattr(app, '_density_compact', False) else 0)
        available = min(app.window.winfo_height(), app.client_limit()[1])
        # A small hysteresis keeps pending Tk geometry requests and one-row /
        # two-row group transitions from repeatedly flipping the density.
        compact_now = available < regular_fixed + (172 if getattr(app, '_density_compact', False) else 140)
        density = (self.mode.get(), compact_now, round(self.scale, 2))
        if getattr(app, '_theme_density', None) == density: return
        app._theme_density = density
        app._density_compact = compact_now
        gaps = compact if compact_now else regular
        app.body.configure(padding=(sp('l'), gaps['top'], sp('l'), 0))
        app.command_frame.grid_configure(pady=(0, gaps['command']))
        app.groups_rule.grid_configure(pady=(0, gaps['tabs']))
        app.table_frame.grid_configure(pady=(gaps['table'], 0))
        ttk.Style(self.root).configure('Group.TButton', padding=(0, p(4), 0, gaps['tab']))
        for widget in (app.status_dot, app.status_label, app.status_info_label, app.total_label):
            widget.grid_configure(pady=gaps['status'])

    def refresh(self, widget):
        c = self.colors
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.configure(background=c['bg'])
            self.titlebar(widget)
        elif isinstance(widget, tk.Canvas):
            widget.configure(background=c['bg'])
        elif isinstance(widget, tk.Text):
            widget.configure(background=c['table'], foreground=c['fg'], insertbackground=c['fg'],
                             selectbackground=c['selection'], selectforeground=c['fg'],
                             relief='flat', borderwidth=0, highlightthickness=1,
                             highlightbackground=c['border'], highlightcolor=c['border'],
                             padx=self.space('m'), pady=self.space('s'), font='SunValleyBodyFont')
        elif isinstance(widget, tk.Menu):
            widget.configure(background=c['table'], foreground=c['fg'],
                             activebackground=c['selection'], activeforeground=c['fg'],
                             selectcolor=c['accent'], disabledforeground=c['disabled_fg'])
        for child in widget.winfo_children():
            self.refresh(child)

    def mapped(self, event):
        if getattr(event.widget, '_sno2md_popup', False): return
        if event.widget is self.root or isinstance(event.widget, tk.Toplevel):
            self.refresh(event.widget)
            if isinstance(event.widget, tk.Toplevel):
                from .gui_layout import fit_dialog
                fit_dialog(event.widget)

    def titlebar(self, window):
        if sys.platform != 'win32' or not window.winfo_ismapped():
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32')
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dark = ctypes.c_int(self.mode.get() == 'dark')
        # Unsupported Windows versions simply return an HRESULT; no OS setting changes.
        dwm.DwmSetWindowAttribute(user32.GetParent(window.winfo_id()), 20,
                                  ctypes.byref(dark), ctypes.sizeof(dark))
