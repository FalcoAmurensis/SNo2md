"""Sun Valley presentation only; theme selection never changes sync settings."""
import sys
import tkinter as tk
from tkinter import ttk

import sv_ttk


PALETTES = {
    'light': dict(bg='#fafafa', fg='#1c1c1c', sub='#606060', table='#ffffff',
                  footer='#f6f6f6', soft='#e9f3fd', accent='#005fb8',
                  green='#39764c', yellow='#986200', red='#a84232', header='#f3f3f3',
                  border='#d8d8d8', line='#eeeeee', control='#ffffff'),
    'dark': dict(bg='#1c1c1c', fg='#fafafa', sub='#bababa', table='#252525',
                 footer='#222222', soft='#203b53', accent='#57c8ff',
                 green='#87cf9b', yellow='#e6b661', red='#ffb4a9', header='#262626',
                 border='#474747', line='#303030', control='#292929'),
}


class Theme:
    def __init__(self, root):
        self.root = root
        from .gui_icons import Icons
        self.icons = Icons(root)
        self.images = {}
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

    def apply(self, mode):
        if mode not in PALETTES:
            raise ValueError(mode)
        self.mode.set(mode)
        sv_ttk.set_theme(mode, root=self.root._root())
        # Leave theme/geometry events to the normal Tk loop. Flushing them here
        # would re-enter layout callbacks while a theme is only partly configured.
        c = self.colors
        style = ttk.Style(self.root)
        # Point fonts follow Tk's existing DPI policy, including Korean fallback.
        for name, size, weight in (('SunValleyBodyFont', 10, 'normal'),
                ('SunValleyCaptionFont', 9, 'normal'), ('SunValleyBodyStrongFont', 10, 'bold'),
                ('SunValleySubtitleFont', 15, 'bold')):
            self.root.tk.call('font', 'configure', name, '-family', '맑은 고딕', '-size', size, '-weight', weight)
        style.configure('Title.TLabel', font='SunValleySubtitleFont')
        style.configure('Muted.TLabel', foreground=c['sub'])
        style.configure('Status.TFrame', background=c['footer'])
        style.configure('Status.TLabel', background=c['footer'], foreground=c['sub'])
        style.configure('Search.TEntry', padding=(self.pixels(34), 3, 4, 4))
        style.configure('Filter.TCombobox', padding=(6, 3, 0, 4))
        style.configure('Treeview', background=c['table'], fieldbackground=c['table'],
                        rowheight=max(33, int(float(self.root.tk.call('tk', 'scaling'))*24)))
        style.map('Treeview', background=[('selected', '#2f60d8')], foreground=[('selected', '#ffffff')])
        # Keep real ttk buttons and their focus/keyboard behavior; only their
        # label/background layout differs from ordinary command buttons.
        self.table_styles(style)
        # A standard ttk image element supplies the CSS-like tab underline.
        self.image_element(style, 'Group.surface', c['bg'], c['bg'],
                           selected=(c['soft'], c['accent']))
        style.layout('Group.TButton', [('Group.surface', {'sticky': 'nswe', 'children': [
            ('Button.padding', {'sticky': 'nswe', 'children': [
                ('Button.focus', {'sticky': 'nswe', 'children': [('Button.label', {'sticky': 'nswe'})]})]})]})])
        style.configure('Group.TButton', padding=(8, 7), anchor='center',
                        background=c['bg'], foreground=c['sub'])
        style.map('Group.TButton', background=[('selected', c['soft']), ('active', c['soft'])],
                  foreground=[('selected', c['accent']), ('active', c['fg'])])
        self.refresh(self.root)

    def image_element(self, style, name, background, line, selected=None):
        """Tile a flat background, keeping a one-pixel bottom rule.

        This changes element painting only. Treeview keeps its native rows,
        headings, scrolling, column hit testing and selection bindings.
        """
        if name in style.element_names(): return
        def image(fill, rule):
            # ttk tiles the center; it does not stretch a 2x2 color swatch.
            # A tiny swatch causes hundreds of thousands of GDI copies when
            # painting a maximized list. Larger flat tiles keep the same pixels
            # and border while bounding the number of native drawing calls.
            photo = tk.PhotoImage(master=self.root, width=256, height=128)
            photo.put(fill, to=(0, 0, 256, 127)); photo.put(rule, to=(0, 127, 256, 128))
            self.images[(self.mode.get(), name, fill, rule)] = photo
            return photo
        normal = image(background, line)
        states = [('selected', image(*selected))] if selected else []
        style.element_create(name, 'image', normal, *states, border=(0, 0, 0, 1),
                             width=0, height=0, sticky='nswe')

    def table_styles(self, style):
        c = self.colors
        self.image_element(style, 'Document.heading', c['header'], c['border'])
        self.image_element(style, 'Document.row', c['table'], c['line'],
                           selected=('#2f60d8', '#5079e1'))
        if 'Document.field' not in style.element_names():
            style.element_create('Document.field', 'from', 'clam', 'Treeview.field')
        style.layout('Document.Treeview', [('Document.field', {'sticky': 'nswe', 'children': [
            ('Treeview.padding', {'sticky': 'nswe', 'children': [('Treeview.treearea', {'sticky': 'nswe'})]})]})])
        style.configure('Document.Treeview', background=c['table'], fieldbackground=c['table'],
                        bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'],
                        borderwidth=1, rowheight=self.pixels(38), font='SunValleyBodyFont')
        style.map('Document.Treeview', background=[('selected', '#2f60d8')], foreground=[('selected', '#ffffff')])
        style.layout('Document.Treeview.Heading', [('Document.heading', {'sticky': 'nswe', 'children': [
            ('Treeheading.padding', {'sticky': 'nswe', 'children': [
                ('Treeheading.image', {'side': 'right'}), ('Treeheading.text', {'sticky': 'we'})]})]})])
        style.configure('Document.Treeview.Heading', font='SunValleyBodyStrongFont',
                        foreground=c['sub'], padding=(self.pixels(13), self.pixels(8)))
        style.layout('Document.Treeview.Row', [('Document.row', {'sticky': 'nswe'})])
        style.configure('Document.Treeview.Cell', padding=(self.pixels(13), 0))

    def button_icon(self, button, name, accent=False):
        c = self.colors
        color = '#ffffff' if accent and self.mode.get() == 'light' else '#101e29' if accent else c['fg']
        key = (name, color, self.pixels(16), c['sub'])
        if getattr(button, '_icon_key', None) == key: return
        button._icon_key = key
        normal = self.icons.get(name, color, self.pixels(16))
        disabled = self.icons.get(name, c['sub'], self.pixels(16))
        button.configure(image=(normal, 'disabled', disabled), compound='left')

    def main(self, app):
        self.button_icon(app.once_button, 'sync', True)
        self.button_icon(app.open_button, 'folder')
        self.button_icon(app.settings_button, 'settings')
        self.button_icon(app.details_button, 'info')
        for button, name in ((app.retry_button, 'sync'), (app.restore_button, 'folder'),
                             (app.resolve_button, 'warning'), (app.policy_button, 'sync')):
            self.button_icon(button, name, True)
        app.search_icon.configure(image=self.icons.get('search', self.colors['sub'], self.pixels(16)))
        for label in (app.search_icon, app.search_placeholder):
            label.configure(background=self.colors['control'], foreground=self.colors['sub'])
        app.search_icon.place(x=self.pixels(10), rely=.5, anchor='w')
        app.search_placeholder.place(x=self.pixels(34), rely=.5, anchor='w')
        app.tree.tag_configure('attention', foreground=self.colors['fg'])
        # Theme changes must not reset widths adjusted by the user. Only the
        # fixed checkbox column follows DPI; flexible columns keep their widths.
        app.tree.column('check', width=self.pixels(46), minwidth=self.pixels(38))
        if not getattr(app, '_columns_initialized', False):
            app.tree.column('name', width=self.pixels(540))
            app.tree.column('status', width=self.pixels(360))
            app._columns_initialized = True
        app.search_hint()
        # apply() recreates theme-local style options even when mode/scale is
        # unchanged, so restore the selected density after every explicit apply.
        app._theme_density = None
        self.density(app)
        if hasattr(app, 'status_cells'): app.status_cells.queue()

    def density(self, app):
        # Normalize the measured fixed content back to regular spacing. This
        # makes the decision independent of the current density (no oscillation).
        rows = max(int(button.grid_info()['row']) for button in app.group_buttons.values())+1
        reduction = 49 + 2*self.pixels(5)*rows
        regular_fixed = app.fixed_height() + (reduction if getattr(app, '_density_compact', False) else 0)
        available = min(app.window.winfo_height(), app.client_limit()[1])
        # A small hysteresis keeps pending Tk geometry requests and one-row /
        # two-row group transitions from repeatedly flipping the density.
        compact = available < regular_fixed + (172 if getattr(app, '_density_compact', False) else 140)
        density = (self.mode.get(), compact, round(self.scale, 2))
        if getattr(app, '_theme_density', None) == density: return
        app._theme_density = density
        app._density_compact = compact
        app.body.configure(padding=(12, 9, 12, 0) if compact else (17, 24, 17, 0))
        app.command_frame.grid_configure(pady=(5, 7) if compact else (10, 15))
        app.groups_frame.grid_configure(pady=(0, 7 if compact else 16))
        ttk.Style(self.root).configure('Group.TButton', padding=(self.pixels(7), self.pixels(5 if compact else 10)))
        for widget in (app.status_dot, app.status_label, app.status_info_label, app.total_label):
            widget.grid_configure(pady=4 if compact else 10)

    def refresh(self, widget):
        c = self.colors
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.configure(background=c['bg'])
            self.titlebar(widget)
        elif isinstance(widget, tk.Canvas):
            widget.configure(background=c['bg'])
        elif isinstance(widget, tk.Text):
            widget.configure(background=c['table'], foreground=c['fg'], insertbackground=c['fg'],
                             selectbackground='#2f60d8', selectforeground='#ffffff',
                             relief='flat', borderwidth=0, highlightthickness=0,
                             padx=10, pady=8, font='SunValleyBodyFont')
        elif isinstance(widget, tk.Menu):
            widget.configure(background=c['bg'], foreground=c['fg'],
                             activebackground=c['soft'], activeforeground=c['accent'],
                             selectcolor=c['accent'], disabledforeground=c['sub'])
        for child in widget.winfo_children():
            self.refresh(child)

    def mapped(self, event):
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
