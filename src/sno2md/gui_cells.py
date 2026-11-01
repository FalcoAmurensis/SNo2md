"""List overlays for Tk 8.6: status icons and a thin scrollbar over the native Treeview."""
import sys
import tkinter as tk
from tkinter import ttk

from .gui_model import PENDING, failed, needs_attention, document_summary

# Icon and palette color per status role; text stays available in the tooltip.
ICONS = {'error': ('dismiss_circle', 'red'), 'warning': ('warning', 'yellow'),
         'working': ('sync', 'accent'), 'pending': ('clock', 'sub'),
         'current': ('check_circle', 'muted'), 'unknown': ('info', 'muted'), 'retired': ('info', 'muted')}


class Tooltip:
    """One borderless popup reused for every status icon; shown only while hovering."""
    def __init__(self, app):
        self.app = app
        self.window = None
        self.job = None

    def schedule(self, widget, text):
        self.cancel()
        if text: self.job = self.app.window.after(400, lambda: self.show(widget, text))

    def cancel(self):
        if self.job is not None:
            try: self.app.window.after_cancel(self.job)
            except tk.TclError: pass
            self.job = None

    def show(self, widget, text):
        self.job = None
        if not widget.winfo_ismapped() or self.app.closing: return
        c, sp = self.app.theme.colors, self.app.theme.space
        if self.window is None or not self.window.winfo_exists():
            self.window = tk.Toplevel(self.app.window)
            self.window._sno2md_popup = True
            self.window.withdraw()
            self.window.overrideredirect(True)
            self.window.attributes('-topmost', True)
            self.label = tk.Label(self.window, borderwidth=0, highlightthickness=1, font='SunValleyCaptionFont')
            self.label.pack()
        self.label.configure(text=text, background=c['table'], foreground=c['fg'],
                             highlightbackground=c['border'], highlightcolor=c['border'],
                             padx=sp('s')+2, pady=sp('xs')+1)
        x = widget.winfo_rootx() + widget.winfo_width()//2 - self.label.winfo_reqwidth()//2
        y = widget.winfo_rooty() + widget.winfo_height() + sp('xs')
        self.window.geometry(f'+{max(0, x)}+{y}')
        self.window.deiconify(); self.window.lift()

    def hide(self, event=None):
        self.cancel()
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()


class StatusCells:
    """Reuse a small pool of native labels for visible status cells only.

    Treeview retains all values, item IDs, headings and selection state. Labels
    forward pointer events to the existing Treeview bindings and never take
    keyboard focus. No document data or action eligibility is changed here.
    """
    def __init__(self, app):
        self.app, self.tree = app, app.tree
        self.labels, self.visible = [], {}
        self.job = None
        self.closed = False
        self.tooltip = Tooltip(app)
        for sequence in ('<Map>', '<Configure>', '<B1-Motion>', '<ButtonRelease-1>', '<MouseWheel>', '<<TreeviewSelect>>'):
            self.tree.bind(sequence, self.queue, add=True)
        self.tree.bind('<MouseWheel>', self.tooltip.hide, add=True)
        self.tree.bind('<Destroy>', self.close, add=True)

    def queue(self, event=None):
        if not self.closed and self.job is None:
            self.job = self.app.window.after_idle(self.refresh)

    def close(self, event=None):
        if event is not None and event.widget is not self.tree: return
        self.closed = True
        self.tooltip.hide()
        if self.job is not None:
            try: self.app.window.after_cancel(self.job)
            except tk.TclError: pass
            self.job = None

    def role(self, key):
        entry = self.app.notes[key]
        item = self.app.observation(key)
        if self.app.group == 'retired': return 'retired'
        if failed(entry): return 'error'
        if needs_attention(entry, item): return 'warning'
        if entry.get('status') in ('converting', 'ready_to_publish'): return 'working'
        if entry.get('status') in PENDING: return 'pending'
        if document_summary({key: entry}, {key: item})['all_current']: return 'current'
        return 'unknown'

    def new_label(self):
        label = tk.Label(self.tree, borderwidth=0, highlightthickness=0, anchor='center',
                         padx=0, pady=0, takefocus=0)
        for sequence in ('<Button-1>', '<ButtonRelease-1>', '<B1-Motion>', '<Button-3>', '<MouseWheel>'):
            label.bind(sequence, lambda event, sequence=sequence, label=label:
                       self.forward(label, event, sequence))
        label.bind('<Enter>', lambda event, label=label: self.tooltip.schedule(label, getattr(label, '_tip', '')))
        label.bind('<Leave>', self.tooltip.hide)
        self.labels.append(label)
        return label

    def forward(self, label, event, sequence):
        if self.closed: return 'break'
        self.tooltip.hide()
        # Hit testing is always done by the live Treeview, not a cached label ID.
        options = dict(x=label.winfo_x()+event.x, y=label.winfo_y()+event.y,
                       rootx=event.x_root, rooty=event.y_root, state=event.state,
                       time=event.time)
        if sequence == '<MouseWheel>': options['delta'] = event.delta
        self.tree.event_generate(sequence, **options)
        return 'break'

    def refresh(self):
        if self.closed: return
        if self.job is not None:
            self.app.window.after_cancel(self.job)
            self.job = None
        width, height = self.tree.winfo_width(), self.tree.winfo_height()
        rowheight = int(self.app.ttk.Style(self.tree).lookup('Document.Treeview', 'rowheight'))
        # Sample the viewport, avoiding an O(total document count) bbox scan.
        rows = []
        for y in [*range(0, height, max(1, rowheight//2)), max(0, height-2)]:
            iid = self.tree.identify_row(y)
            if iid and iid not in rows: rows.append(iid)
        selected = set(self.tree.selection())
        theme = self.app.theme
        colors = theme.colors
        size = theme.pixels(16)
        visible = {}
        for iid in rows:
            key = self.app.tree_keys.get(iid)
            if key not in self.app.notes: continue
            box = self.tree.bbox(iid, 'status')
            if not box: continue
            x, y, w, h = box
            # Treeview leaves a partly visible last row blank; so does its icon.
            if y + h > height: continue
            w, h = min(w, width-x-1), h-1
            if w <= 0 or h <= 0: continue
            index = len(visible)
            label = self.labels[index] if index < len(self.labels) else self.new_label()
            name, color = ICONS[self.role(key)]
            background = colors['selection'] if iid in selected else colors['table']
            paint = (name, colors[color], background, size)
            if getattr(label, '_paint', None) != paint:
                label.configure(image=theme.icons.get(name, colors[color], size), background=background)
                label._paint = paint
            label._tip = self.app.status_texts.get(key, '')
            label._role = color
            geometry = (x,y,w,h)
            if getattr(label,'_cell_box',None) != geometry:
                label.place(x=x, y=y, width=w, height=h, bordermode='ignore')
                label._cell_box = geometry
            visible[iid] = label
        for label in self.labels[len(visible):]:
            if getattr(label,'_cell_box',None) is not None:
                label.place_forget(); label._cell_box = None
        self.visible = visible


def always_show_scrollbars():
    """Windows Settings > Accessibility > Visual effects > Always show scrollbars."""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Accessibility') as key:
            return winreg.QueryValueEx(key, 'DynamicScrollbars')[0] == 0
    except OSError:
        return False


class ListScrollbar:
    """Thin overlay scrollbar shown while the pointer is over the list or it scrolls.

    It is placed over the list's right edge so showing or hiding it never changes
    the list width. Only one hide timer runs, and only after the pointer leaves.
    """
    def __init__(self, app, frame):
        self.app, self.tree = app, app.tree
        self.bar = ttk.Scrollbar(frame, orient='vertical', style='Thin.Vertical.TScrollbar',
                                 command=self.tree.yview, takefocus=0)
        self.always = always_show_scrollbars()
        self.position = (0.0, 1.0)
        self.active = self.dragging = False
        self.job = None
        for widget in (self.tree, self.bar):
            widget.bind('<Enter>', self.enter, add=True)
            widget.bind('<Leave>', self.leave, add=True)
        self.tree.bind('<MouseWheel>', self.enter, add=True)
        self.bar.bind('<ButtonPress-1>', lambda e: setattr(self, 'dragging', True), add=True)
        self.bar.bind('<ButtonRelease-1>', self.release, add=True)

    def set(self, first, last):
        self.position = (float(first), float(last))
        self.bar.set(first, last)
        self.refresh()

    def overflow(self):
        return self.position[0] > 0 or self.position[1] < 1

    def enter(self, event=None):
        self.active = True
        if self.job is not None:
            self.app.window.after_cancel(self.job); self.job = None
        self.refresh()

    def leave(self, event=None):
        if self.job is None: self.job = self.app.window.after(900, self.expire)

    def release(self, event=None):
        self.dragging = False
        self.leave()

    def expire(self):
        self.job = None
        if self.dragging: return
        try:
            pointer = self.app.window.winfo_containing(*self.app.window.winfo_pointerxy())
        except (KeyError, tk.TclError):
            pointer = None
        # Leave also fires when moving onto a status icon inside the list.
        inside = pointer is not None and str(pointer).startswith((str(self.tree), str(self.bar)))
        self.active = inside
        self.refresh()

    def heading_height(self):
        for y in range(0, self.app.theme.pixels(80)):
            if self.tree.identify_region(self.app.theme.pixels(8), y) != 'heading':
                return y
        return 0

    def refresh(self):
        show = self.overflow() and (self.always or self.active or self.dragging)
        if show:
            top = self.heading_height()
            self.bar.place(in_=self.tree, relx=1, x=-1, y=top, relheight=1, height=-top-1, anchor='ne')
            self.bar.lift()
        elif self.bar.winfo_manager():
            self.bar.place_forget()
