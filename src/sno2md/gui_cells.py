"""Colored status text for Tk 8.6, retaining the native Treeview underneath."""
import tkinter as tk

from .gui_model import needs_attention, document_summary


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
        for sequence in ('<Map>', '<Configure>', '<B1-Motion>', '<ButtonRelease-1>', '<MouseWheel>', '<<TreeviewSelect>>'):
            self.tree.bind(sequence, self.queue, add=True)
        self.tree.bind('<Destroy>', self.close, add=True)

    def queue(self, event=None):
        if not self.closed and self.job is None:
            self.job = self.app.window.after_idle(self.refresh)

    def close(self, event=None):
        if event is not None and event.widget is not self.tree: return
        self.closed = True
        if self.job is not None:
            try: self.app.window.after_cancel(self.job)
            except tk.TclError: pass
            self.job = None

    def role(self, key):
        entry = self.app.notes[key]
        item = self.app.observation(key)
        if self.app.group == 'retired': return 'sub'
        if entry['status'] in ('error', 'failed'): return 'red'
        if needs_attention(entry, item): return 'yellow'
        if document_summary({key:entry},{key:item})['all_current']: return 'green'
        return 'sub'

    def new_label(self):
        label = tk.Label(self.tree, borderwidth=0, highlightthickness=0,
                         anchor='nw', pady=0, takefocus=0, font='SunValleyBodyFont')
        for sequence in ('<Button-1>', '<ButtonRelease-1>', '<B1-Motion>', '<Button-3>', '<MouseWheel>'):
            label.bind(sequence, lambda event, sequence=sequence, label=label:
                       self.forward(label, event, sequence))
        self.labels.append(label)
        return label

    def forward(self, label, event, sequence):
        if self.closed: return 'break'
        # Hit testing is always done by the live Treeview, not a cached label ID.
        options = dict(x=label.winfo_x()+event.x, y=label.winfo_y()+event.y,
                       rootx=event.x_root, rooty=event.y_root, state=event.state)
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
        linespace = int(self.tree.tk.call('font','metrics','SunValleyBodyFont','-linespace'))
        # Sample the viewport, avoiding an O(total document count) bbox scan.
        rows = []
        for y in [*range(0, height, max(1, rowheight//2)), max(0, height-2)]:
            iid = self.tree.identify_row(y)
            if iid and iid not in rows: rows.append(iid)
        selected = set(self.tree.selection())
        colors = self.app.theme.colors
        visible = {}
        for iid in rows:
            key = self.app.tree_keys.get(iid)
            if key not in self.app.notes: continue
            box = self.tree.bbox(iid, 'status')
            if not box: continue
            x, y, w, h = box
            padding_y = max(0,(h-linespace)//2)
            w, h = min(w, width-x-1), min(h-1, height-y-1)
            if w <= 0 or h <= 0: continue
            index = len(visible)
            label = self.labels[index] if index < len(self.labels) else self.new_label()
            foreground = '#ffffff' if iid in selected else colors[self.role(key)]
            background = '#2f60d8' if iid in selected else colors['table']
            paint = (self.tree.set(iid, 'status'), foreground, background, self.app.theme.pixels(13), padding_y)
            if getattr(label, '_paint', None) != paint:
                label.configure(text=paint[0], foreground=paint[1], background=paint[2], padx=paint[3], pady=paint[4])
                label._paint = paint
            geometry = (x,y,w,h)
            if getattr(label,'_cell_box',None) != geometry:
                label.place(x=x, y=y, width=w, height=h, bordermode='ignore')
                label._cell_box = geometry
            visible[iid] = label
        for label in self.labels[len(visible):]:
            if getattr(label,'_cell_box',None) is not None:
                label.place_forget(); label._cell_box = None
        self.visible = visible
