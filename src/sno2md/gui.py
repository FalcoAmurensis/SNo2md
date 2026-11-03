"""Korean desktop UI. Tk stays on the main thread; all document I/O stays in workers."""
import argparse
import json
import os
from pathlib import Path
from queue import Empty
import sys

from .desktop import DesktopController
from .locations import state_directory
from .sync_observation import group
from .gui_model import (STATUS, GROUPS, FILTERS, execution, close_action, failed, format_time, synced_at,
                        matches, targets, guidance, status_text, document_summary, needs_attention, PENDING)
from .gui_text import NFCStringVar, NFCText, display, ask_name
from .gui_layout import client_limit, elide, work_area, window_dpi

WORKING = ('converting', 'ready_to_publish')


class Application:
    def __init__(self, window, controller, native, tray=None, startup=False, dpi_aware=False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.window, self.controller, self.native, self.tray = window, controller, native, tray
        self.startup = startup
        self.dpi_aware = dpi_aware
        self.dpi = window_dpi(window) if dpi_aware else 96
        self.config = None
        self.notes, self.observations, self.tree_keys = {}, {}, {}
        self.checked = set()
        self.anchor = None
        self.selecting = False
        self.group = 'active'
        self.settings_dialog = self.resolution_view = None
        self.review_dialog = None
        self.pending_reviews = {}
        self.offered_reviews = set()
        self.resume_review_watch = False
        self.pending_offer = None
        self.pending_results = []
        self.locations = {'backup': [], 'vault': []}
        self.selected_paths = {}
        self.closing = self.initialized = self.failed = False
        self.log_lines = []
        # Progress of the current run, derived from observed note states only.
        self.run_keys, self.working_names, self.status_texts = set(), [], {}
        self.last_sync = self.latest_success = None
        self.chip_counts = {'확인 필요': 0, '실패': 0}
        self.search = NFCStringVar(window)
        self.state_filter = NFCStringVar(window, '전체')
        self.banner = NFCStringVar(window, '설정을 불러오는 중…')
        self.run_label = NFCStringVar(window, '작업 중')
        self.close_hint = NFCStringVar(window)
        self.selection_summary = NFCStringVar(window, '선택 0개')
        self.result_count = NFCStringVar(window, '검색 결과 0개')
        self.empty_text = NFCStringVar(window)
        self.attention_summary = NFCStringVar(window)
        self.status_summary = NFCStringVar(window)
        self.status_info = NFCStringVar(window)
        self.total_summary = NFCStringVar(window)
        self.summary = document_summary({}, {})
        self.backup_summary = NFCStringVar(window, '삼성노트 백업: 설정이 필요합니다.')
        self.vault_summary = NFCStringVar(window, '저장 폴더/Vault: 설정이 필요합니다.')
        self.folder_summary = NFCStringVar(window)
        window.title('SNo2md')
        factor = self.dpi/96
        self.base_size = (round(700*factor), round(480*factor))
        window.geometry(f'{round(1000*factor)}x{round(650*factor)}'); window.minsize(*self.base_size)
        from .gui_theme import Theme
        self.theme = Theme(window)
        sp = self.theme.space
        # The status bar spans the whole window edge, like a document editor's.
        self.status_frame = status = ttk.Frame(window, style='Status.TFrame')
        status.pack(side='bottom', fill='x')
        self.body = body = ttk.Frame(window)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1); body.rowconfigure(2, weight=1)
        # One command bar: sync work on the left, settings and the app menu on the right.
        self.command_frame = self.header = actions = ttk.Frame(body)
        actions.grid(row=0, column=0, sticky='ew')
        actions.columnconfigure(3, weight=1)
        self.once_button = ttk.Button(actions, text='지금 동기화', command=self.sync_once, style='Accent.TButton')
        self.watch_button = ttk.Button(actions, text='자동 동기화 시작', command=self.toggle)
        self.open_button = ttk.Button(actions, text='Vault 열기', command=self.open_vault)
        self.run_buttons = (self.once_button, self.watch_button, self.open_button)
        self.once_button.grid(row=0, column=0)
        self.watch_button.grid(row=0, column=1, padx=(sp('xs'), 0))
        self.open_button.grid(row=0, column=2, padx=(sp('xs'), 0))
        self.settings_button = ttk.Button(actions, text='설정', command=self.edit_settings)
        self.settings_button.grid(row=0, column=4, padx=(sp('xs'), 0))
        self.global_menu = tk.Menu(window, tearoff=False, postcommand=self.controls)
        self.global_menu.add_command(label='진단 및 로그', command=lambda: self.details(global_view=True))
        self.global_menu.add_command(label='경로 진단', command=lambda: self.diagnose_paths(set()))
        self.global_menu.add_command(label='최근 보존본 보기', command=self.preserved_copies)
        self.global_menu.add_separator()
        self.global_menu.add_command(label='창 닫기 동작 안내', command=self.explain_close)
        self.global_menu.add_separator()
        for label, mode in (('라이트', 'light'), ('다크', 'dark')):
            self.global_menu.add_radiobutton(label=label+' 테마', variable=self.theme.mode, value=mode,
                                            command=lambda mode=mode: self.change_theme(mode))
        self.global_more = ttk.Menubutton(actions, text='메뉴', menu=self.global_menu)
        self.global_more.grid(row=0, column=5, padx=(sp('xs'), 0))
        self.filter_area = filter_area = ttk.Frame(body); filter_area.grid(row=1, column=0, sticky='ew')
        filter_area.columnconfigure(0, weight=1)
        self.groups_frame = ttk.Frame(filter_area); self.groups_frame.grid(row=0, column=0, columnspan=4, sticky='ew')
        self.group_buttons = {}
        for index, (key, label) in enumerate(GROUPS.items()):
            button = ttk.Button(self.groups_frame, text=label, style='Group.TButton', command=lambda key=key: self.change_group(key))
            button.grid(row=0, column=index, sticky='w', padx=(0, sp('xl')))
            self.group_buttons[key] = button
        self.groups_rule = ttk.Separator(filter_area)
        self.groups_rule.grid(row=1, column=0, columnspan=4, sticky='ew')
        self.search_box = ttk.Entry(filter_area, textvariable=self.search, width=1, style='Search.TEntry')
        self.search_box.grid(row=2, column=0, sticky='ew')
        self.search_icon = tk.Label(self.search_box, borderwidth=0, highlightthickness=0, padx=0, pady=0, cursor='xterm')
        self.search_placeholder = tk.Label(self.search_box, text='문서 검색', borderwidth=0, highlightthickness=0,
                                           padx=0, pady=0, cursor='xterm', font='SunValleyBodyFont')
        for label in (self.search_icon, self.search_placeholder):
            label.bind('<Button-1>', lambda e: self.search_box.focus_set())
        self.search.trace_add('write', self.search_hint)
        # Problem filters sit next to search; each hides itself at zero.
        self.attention_chip = ttk.Button(filter_area, style='Chip.TButton', command=lambda: self.toggle_filter('확인 필요'))
        self.failure_chip = ttk.Button(filter_area, style='Chip.TButton', command=lambda: self.toggle_filter('실패'))
        self.filter_chips = {'확인 필요': self.attention_chip, '실패': self.failure_chip}
        self.filter_box = ttk.Combobox(filter_area, textvariable=self.state_filter, values=FILTERS, state='readonly', width=10, style='Filter.TCombobox')
        self.filter_box.grid(row=2, column=3, padx=(sp('xs'), 0))
        self.table_frame = table = ttk.Frame(body); table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1); table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('check', 'name', 'status', 'time'), show='headings', selectmode='extended', height=5, style='Document.Treeview')
        self.tree.heading('check', text='☐', command=self.toggle_all_checked)
        self.tree.column('check', width=40, minwidth=36, stretch=False, anchor='center')
        self.tree.heading('name', text='문서명', anchor='w'); self.tree.column('name', width=320, minwidth=120)
        self.tree.heading('status', text='상태', anchor='center'); self.tree.column('status', width=56, minwidth=48, stretch=False, anchor='center')
        self.tree.heading('time', text='동기화 일시', anchor='w'); self.tree.column('time', width=150, minwidth=90, stretch=False)
        self.tree.grid(row=0, column=0, sticky='nsew')
        from .gui_cells import ListScrollbar, StatusCells
        self.list_scroll = ListScrollbar(self, table)
        def scrolled(first, last):
            position = (first,last)
            if getattr(self,'tree_scroll_position',None) != position:
                self.tree_scroll_position = position
                self.list_scroll.set(first, last)
                if hasattr(self, 'status_cells'): self.status_cells.queue()
        self.tree.configure(yscrollcommand=scrolled)
        self.empty_label = ttk.Label(self.tree, textvariable=self.empty_text, style='Empty.TLabel', anchor='center', justify='center')
        self.tree.bind('<Button-1>', self.click_check)
        self.tree.bind('<space>', self.space_check)
        self.tree.bind('<Control-a>', self.select_all)
        self.tree.bind('<Control-A>', self.select_all)
        self.tree.bind('<<TreeviewSelect>>', self.selection_changed)
        self.tree.bind('<Button-3>', self.context_menu)
        self.tree.bind('<Shift-F10>', self.keyboard_context_menu)
        self.tree.bind('<App>', self.keyboard_context_menu)
        self.action_frame = ttk.Frame(body); self.action_frame.grid(row=3, column=0, sticky='ew')
        self.action_frame.columnconfigure(0, weight=1)
        self.selection_label = ttk.Label(self.action_frame, textvariable=self.selection_summary, style='Muted.TLabel')
        self.selection_label.grid(row=0, column=0, sticky='w')
        self.result_label = ttk.Label(self.action_frame, textvariable=self.result_count, width=1, style='Muted.TLabel')
        self.retry_button = ttk.Button(self.action_frame, command=self.retry)
        self.restore_button = ttk.Button(self.action_frame, command=self.restore)
        self.policy_button = ttk.Button(self.action_frame, command=self.policy)
        self.resolve_button = ttk.Button(self.action_frame, command=self.resolve)
        self.rename_button = ttk.Button(self.action_frame, text='다른 이름으로 저장', command=self.rename)
        self.details_button = ttk.Button(self.action_frame, text='문제 보기', command=self.details)
        self.document_buttons = (self.retry_button, self.restore_button, self.policy_button, self.resolve_button, self.rename_button, self.details_button)
        self.action_menu = tk.Menu(window, tearoff=False, postcommand=self.controls)
        for label, command in (('다시 시도', self.retry), ('저장 폴더/Vault에 다시 저장', self.restore),
                               ('관리 중단', self.policy), ('Markdown 충돌 해결', self.resolve),
                               ('다른 이름으로 저장', self.rename), ('문제 자세히 보기', self.details)):
            self.action_menu.add_command(label=label, command=command)
        self.action_more = ttk.Menubutton(self.action_frame, text='기타 작업', menu=self.action_menu)
        self.groups_frame.bind('<Configure>', lambda e: self.group_labels())
        # Status bar: state | progress or last sync | current document or total.
        status.columnconfigure(6, weight=1)
        self.status_dot = ttk.Label(status, style='Status.TLabel')
        self.status_dot.grid(row=0, column=0, sticky='w', padx=(sp('l'), sp('s')))
        self.status_label = ttk.Label(status, textvariable=self.run_label, style='StatusStrong.TLabel')
        self.status_label.grid(row=0, column=1, sticky='w')
        self.status_separator = ttk.Separator(status, orient='vertical')
        self.status_separator.grid(row=0, column=2, sticky='ns', padx=sp('m'), pady=sp('s'))
        self.status_info_label = ttk.Label(status, textvariable=self.status_info, style='Status.TLabel')
        self.status_info_label.grid(row=0, column=3, sticky='w')
        self.end_separator = ttk.Separator(status, orient='vertical')
        self.end_separator.grid(row=0, column=4, sticky='ns', padx=sp('m'), pady=sp('s'))
        self.total_label = ttk.Label(status, textvariable=self.total_summary, style='Status.TLabel')
        self.total_label.grid(row=0, column=5, sticky='w', padx=(0, sp('l')))
        status.bind('<Configure>', lambda e: self.update_status())
        self.banner.trace_add('write', lambda *_: self.update_status())
        window.protocol('WM_DELETE_WINDOW', self.on_close)
        window.report_callback_exception = lambda kind, value, tb: self.error(f'{kind.__name__}: {value}')
        self.search.trace_add('write', lambda *_: self.filter_changed())
        self.state_filter.trace_add('write', lambda *_: self.filter_changed())
        self.status_cells = StatusCells(self)
        self.theme.main(self)
        window.bind('<Configure>', self.window_resized, add=True)
        self.controls(); window.after(100, self.poll)
        controller.launch(controller.initialize, kind='initialize')

    def size(self, view, width, height, min_width, min_height):
        """Dialog size in 96 DPI units, scaled when the process draws at the real DPI."""
        factor = self.dpi/96
        view.geometry(f'{round(width*factor)}x{round(height*factor)}')
        view.minsize(round(min_width*factor), round(min_height*factor))

    def change_theme(self, mode):
        self.theme.apply(mode)
        self.theme.main(self)
        self.controls()
        self.group_labels()
        if self.settings_dialog: self.settings_dialog.queue_scroll()

    def search_hint(self, *_):
        if self.search.get(): self.search_placeholder.place_forget()
        else: self.search_placeholder.place(x=self.theme.pixels(34), rely=.5, anchor='w')

    def toggle_filter(self, name):
        self.state_filter.set('전체' if self.state_filter.get() == name else name)

    def edit_settings(self):
        if self.review_dialog:return
        if self.controller.busy or self.closing or self.resolution_view: return
        if self.settings_dialog:
            self.settings_dialog.window.lift(); return
        from .settings_dialog import SettingsDialog
        self.settings_dialog = SettingsDialog(self)
        self.controls()

    def paths_saved(self):
        return self.config is not None and self.settings_dialog is None and not self.closing

    def observation(self, key):
        entry = self.notes[key]
        return self.observations.get(key, {'source_exists': False if entry['status'] == 'missing' else None,
                                          'missing': ['pdf', 'md'] if entry['status'] == 'missing_output' else [],
                                          'excluded': entry['status'] == 'excluded'})

    def checked_targets(self, statuses=None):
        visible = set(self.tree_keys.values())
        return {key for key in self.checked & visible if statuses is None or self.notes[key]['status'] in statuses}

    def single_target(self):
        keys = self.checked_targets()
        return next(iter(keys)) if len(keys) == 1 else None

    def action_targets(self, action):
        return targets(action, self.checked_targets(), self.notes, {key: self.observation(key) for key in self.notes})

    def refresh_checks(self):
        self.selecting = True
        try:
            selected = []
            for iid, key in self.tree_keys.items():
                checked = key in self.checked
                self.tree.set(iid, 'check', '☑' if checked else '☐')
                if checked: selected.append(iid)
            if set(self.tree.selection()) != set(selected): self.tree.selection_set(selected)
            count = len(selected); visible = len(self.tree_keys)
            self.tree.heading('check', text='☑' if count and count == visible else '▣' if count else '☐')
        finally:
            self.selecting = False
        self.controls()
        self.status_cells.refresh()

    def selection_changed(self, event=None):
        if self.selecting: return
        selected = {self.tree_keys[iid] for iid in self.tree.selection() if iid in self.tree_keys}
        if selected != self.checked:
            self.checked = selected; self.refresh_checks()
        else: self.controls()

    def toggle_check(self, iid):
        key = self.tree_keys.get(iid)
        if key is None: return
        self.checked.symmetric_difference_update({key}); self.anchor = key
        self.tree.focus(iid); self.refresh_checks()

    def click_check(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != 'cell': return
        iid = self.tree.identify_row(event.y)
        if iid not in self.tree_keys: return
        self.tree.focus_set(); self.tree.focus(iid)
        key = self.tree_keys[iid]
        if self.tree.identify_column(event.x) == '#1':
            self.toggle_check(iid); return 'break'
        control, shift = bool(event.state & 0x0004), bool(event.state & 0x0001)
        visible = list(self.tree_keys.values())
        if shift:
            anchor = self.anchor if self.anchor in visible else key
            start, end = sorted((visible.index(anchor), visible.index(key)))
            selected = set(visible[start:end+1])
            self.checked = self.checked | selected if control else selected
        elif control:
            self.checked.symmetric_difference_update({key}); self.anchor = key
        else:
            self.checked = {key}; self.anchor = key
        self.refresh_checks()
        return 'break'

    def space_check(self, event=None):
        self.toggle_check(self.tree.focus()); return 'break'

    def select_all(self, event=None):
        self.checked = set(self.tree_keys.values()); self.refresh_checks(); return 'break'

    def toggle_all_checked(self):
        visible = set(self.tree_keys.values())
        self.checked = set() if visible <= self.checked else visible
        self.refresh_checks()

    def change_group(self, value):
        self.group = value; self.filter_changed()

    def filter_changed(self):
        self.checked.clear(); self.anchor = None
        self.update_state({'notes': self.notes, 'observations': self.observations})

    def group_labels(self):
        counts = getattr(self, 'group_counts', {})
        for key, button in self.group_buttons.items():
            count = counts.get(key, 0)
            button.configure(text=f'{GROUPS[key]} {count}',
                             style='Zero.Group.TButton' if count == 0 and key != self.group else 'Group.TButton')
            button.state(['selected', '!pressed'] if key == self.group else ['!selected', '!pressed'])
        gap = self.theme.space('xl')
        columns = 4 if sum(b.winfo_reqwidth()+gap for b in self.group_buttons.values()) <= self.groups_frame.winfo_width() else 2
        for index, button in enumerate(self.group_buttons.values()):
            button.grid(row=index//columns, column=index%columns, sticky='w', padx=(0, gap), pady=0)

    def update_chips(self):
        """Problem filters: visible with a count, or while their filter is active."""
        sp = self.theme.space
        current = self.state_filter.get()
        for column, (name, chip) in enumerate(self.filter_chips.items(), start=1):
            count = self.chip_counts.get(name, 0)
            text = f'{name} {count}'
            if chip.cget('text') != text: chip.configure(text=text)
            chip.state(['selected'] if current == name else ['!selected'])
            if count or current == name:
                if not chip.winfo_manager(): chip.grid(row=2, column=column, padx=(sp('s') if column == 1 else sp('xs'), 0))
            elif chip.winfo_manager(): chip.grid_remove()

    def layout_actions(self, event=None):
        if getattr(self, 'layout_job', None) is None:
            def fit():
                self.layout_job = None
                self.fit_layout()
            self.layout_job = self.window.after_idle(fit)
        chosen = bool(self.checked_targets())
        filtering = bool(self.search.get().strip()) or self.state_filter.get() != '전체'
        candidates = (self.policy_button,) if self.group == 'retired' else (self.resolve_button, self.restore_button, self.retry_button)
        primary = next((b for b in candidates if str(b.cget('state')) == 'normal'), None) if chosen else None
        height = max(widget.winfo_reqheight() for widget in (*self.document_buttons, self.action_more))
        height += 2*self.theme.space('s')
        layout = (chosen, primary, height, filtering)
        if getattr(self, 'action_layout', None) == layout: return
        self.action_layout = layout
        for button in self.document_buttons: button.grid_remove()
        self.action_frame.configure(height=height)
        self.action_frame.grid_propagate(False)
        self.action_frame.rowconfigure(0, minsize=height)
        sp = self.theme.space
        if not chosen:
            self.selection_label.grid_remove(); self.action_more.grid_remove()
            if filtering: self.result_label.grid(row=0, column=0, sticky='ew')
            else: self.result_label.grid_remove()
            self.primary_button = None
            return
        self.result_label.grid_remove()
        self.selection_label.grid()
        self.primary_button = primary
        if self.primary_button:
            self.primary_button.grid(row=0, column=1, padx=(sp('xs'), 0))
        self.details_button.grid(row=0, column=2, padx=(sp('xs'), 0))
        self.action_more.grid(row=0, column=3, padx=(sp('xs'), 0))

    def window_resized(self, event):
        if event.widget is not self.window: return
        if self.dpi_aware:
            dpi = window_dpi(self.window)
            if dpi != self.dpi: self.rescale(dpi); return
        size = event.width, event.height
        if getattr(self, '_window_size', None) != size:
            self._window_size = size
            self.layout_actions()

    def rescale(self, dpi):
        """Follow a monitor DPI change: fonts, sprites, spacing and the window size."""
        ratio = dpi/self.dpi
        self.dpi = dpi
        self.window.tk.call('tk', 'scaling', dpi/72)
        self.base_size = (round(700*dpi/96), round(480*dpi/96))
        if self.window.state() == 'normal':
            width, height = self.window.winfo_width(), self.window.winfo_height()
            self.window.geometry(f'{round(width*ratio)}x{round(height*ratio)}')
        self.change_theme(self.theme.mode.get())

    def update_status(self):
        import tkinter.font as font
        values = self.summary
        mode = execution(self.controller.busy, self.controller.kind, self.controller.stop_event.is_set())
        total = values['total']
        working = mode == 'working' or (mode == 'watch' and values['pending'])
        if self.failed:
            kind = 'error'
        elif not self.initialized:
            kind = 'idle'
        elif not self.config:
            kind = 'setup'
        elif mode == 'stopping':
            kind = 'stopping'
        elif working:
            kind = 'sync'
        else:
            kind = 'watch' if mode == 'watch' else 'idle'
        self.status_indicator = kind
        run = {'setup': '설정 필요', 'stopping': '중지 중', 'sync': '동기화 중', 'watch': '자동 동기화 중'}.get(
            kind, self.run_label.get() if kind == 'error' else '대기 중' if self.initialized else '준비 중')
        if self.run_label.get() != run and kind != 'error': self.run_label.set(run)
        image = self.theme.dot(kind)
        if getattr(self.status_dot, '_image', None) is not image:
            self.status_dot.configure(image=image); self.status_dot._image = image
        banner = self.banner.get()
        if banner:
            info = banner
        elif kind == 'sync' and self.run_keys:
            done = len(self.run_keys - {k for k, e in self.notes.items() if e.get('status') in PENDING})
            info = f'{done} / {len(self.run_keys)}개 처리'
        elif kind == 'sync':
            info = '변경 사항 확인 중'
        else:
            latest = max((t for t in (self.last_sync, self.latest_success) if t), default=None)
            info = f'마지막 동기화 {format_time(latest)}' if latest else '아직 동기화하지 않음'
        if kind == 'sync' and self.working_names:
            end = f'현재: {self.working_names[0]}' + (f' 외 {len(self.working_names)-1}개' if len(self.working_names) > 1 else '')
        else:
            end = f'전체 문서 {total}개'
        # Diagnostics keep a complete one-line summary independent of the bar's width.
        parts = [run]
        self.attention_summary.set(f'확인 필요 {values["attention"]}개' if values['attention'] else '')
        if values['attention']: parts.append(self.attention_summary.get())
        if values['pending']:
            parts.append(f'{"처리 중" if values["processing"] else "처리 대기"} {values["pending"]}개')
        elif values['all_current'] and not self.failed and mode in ('idle', 'watch'):
            parts.append('모든 문서 최신 상태')
        parts.append(f'전체 {total}개')
        self.status_summary.set(', '.join(parts))
        # Fit the middle text with real font metrics; the end column is dropped last.
        style = self.ttk.Style(self.window)
        actual = font.Font(root=self.window, font=style.lookup('TLabel', 'font'))
        sp = self.theme.space
        fixed = sum(w.winfo_reqwidth() for w in (self.status_dot, self.status_label))
        fixed += sp('l') + sp('s') + 2*(2*sp('m')+2) + sp('l')
        available = max(0, self.status_frame.winfo_width() - fixed - actual.measure(end))
        if available < actual.measure('…')*6:
            end = ''
            available = max(0, self.status_frame.winfo_width() - fixed)
        info = elide(info, available, actual.measure)
        if self.status_info.get() != info: self.status_info.set(info)
        if self.total_summary.get() != end: self.total_summary.set(end)
        if end and not self.end_separator.winfo_manager(): self.end_separator.grid()
        elif not end and self.end_separator.winfo_manager(): self.end_separator.grid_remove()

    def client_limit(self):
        return client_limit(self.window)

    def fixed_height(self):
        """Measured fixed rows, including grid gaps and the body's padding."""
        fixed = (self.command_frame, self.filter_area, self.action_frame)
        def number(value): return self.window.winfo_pixels(value)
        def pads(value):
            pad = (value,) if isinstance(value, int) else self.window.tk.splitlist(value)
            return 2*number(pad[0]) if len(pad) == 1 else sum(map(number, pad))
        height = 0
        for widget in fixed:
            if not widget.winfo_manager(): continue
            height += widget.winfo_reqheight() + pads(widget.grid_info().get('pady', 0))
        height += self.status_frame.winfo_reqheight()
        padding = self.window.tk.splitlist(self.body.cget('padding'))
        height += sum(number(padding[i]) for i in (1,3)) if len(padding) == 4 else 0
        height += pads(self.table_frame.grid_info().get('pady', 0))
        return height

    def fit_layout(self):
        if self.closing: return
        self.theme.density(self)
        width, height = self.client_limit()
        required = self.fixed_height()
        minimum = (min(self.base_size[0], width), min(height, max(self.base_size[1], required + self.theme.pixels(120))))
        if self.window.minsize() != minimum: self.window.minsize(*minimum)
        # Shrink the initial window if the taskbar / monitor cannot contain it.
        current = self.window.winfo_width(), self.window.winfo_height()
        if self.window.state() == 'normal' and (current[0] > width or current[1] > height):
            left,top,_,_=work_area(self.window)
            self.window.geometry(f'{min(current[0],width)}x{min(current[1],height)}{left:+d}{top:+d}')

    def popup_actions(self, x, y):
        if self.closing or not self.checked_targets(): return 'break'
        self.controls()
        try: self.action_menu.tk_popup(x, y)
        finally: self.action_menu.grab_release()
        return 'break'

    def context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid not in self.tree_keys: return 'break'
        self.context_target(iid)
        return self.popup_actions(event.x_root, event.y_root)

    def context_target(self, iid):
        key = self.tree_keys[iid]
        if key not in self.checked:
            self.checked = {key}; self.anchor = key
        self.tree.focus_set(); self.tree.focus(iid); self.refresh_checks()

    def keyboard_context_menu(self, event=None):
        iid = self.tree.focus()
        if iid not in self.tree_keys: return 'break'
        self.tree.see(iid)
        box = self.tree.bbox(iid)
        if not box: return 'break'
        x, y, _, height = box
        self.context_target(iid)
        return self.popup_actions(self.tree.winfo_rootx()+x, self.tree.winfo_rooty()+y+height)

    def explain_close(self):
        if self.closing: return
        from tkinter import messagebox
        messagebox.showinfo('창 닫기 동작', self.close_hint.get(), parent=self.window)

    def preserved_copies(self):
        self.results_dialog([{'source': key, 'status': 'synced', 'copy': entry['last_preserved_copy']}
                             for key, entry in self.notes.items() if entry.get('last_preserved_copy')])

    @staticmethod
    def menu_entry(menu, index, **options):
        # Reconfiguring unchanged Windows menu entries rebuilds the native menu
        # while it is posted, interrupting cascade/keyboard navigation.
        changes = {key: value for key, value in options.items()
                   if str(menu.entrycget(index, key)) != str(value)}
        if changes: menu.entryconfigure(index, **changes)

    def controls(self):
        busy = self.controller.busy
        mode = execution(busy, self.controller.kind, self.controller.stop_event.is_set())
        idle = self.initialized and not busy and not self.closing and not self.settings_dialog and not self.resolution_view and not self.review_dialog
        self.settings_button.configure(state='normal' if self.initialized and not busy and not self.closing and not self.resolution_view and not self.review_dialog else 'disabled')
        self.once_button.configure(state='normal' if idle and self.config else 'disabled')
        self.open_button.configure(state='normal' if self.config and not self.closing else 'disabled')
        self.watch_button.configure(text='중지 중…' if mode == 'stopping' else '자동 동기화 중지' if mode == 'watch' else '자동 동기화 시작',
                                    state='normal' if not self.closing and (mode == 'watch' or idle and self.config) else 'disabled')
        self.theme.button_icon(self.watch_button, 'stop' if mode in ('watch', 'stopping') else 'play')
        chosen = self.checked_targets()
        self.selection_summary.set(f'선택 {len(chosen)}개')
        policy = 'resume' if self.group == 'retired' else 'retire'
        for index, (button, action, label, short) in enumerate(((self.retry_button, 'retry', '다시 시도', '다시 시도'),
                                      (self.restore_button, 'restore', '저장 폴더/Vault에 다시 저장', '다시 저장'),
                                      (self.policy_button, policy, '관리 재개' if policy == 'resume' else '관리 중단', '관리 재개' if policy == 'resume' else '관리 중단'),
                                      (self.resolve_button, 'resolve', 'Markdown 충돌 해결', '충돌 해결'))):
            accepted, _ = self.action_targets(action)
            state = 'normal' if idle and accepted else 'disabled'
            button.configure(text=f'{short} ({len(accepted)})', state=state)
            self.menu_entry(self.action_menu, index, label=f'{label} ({len(accepted)})', state=state)
        accepted, _ = self.action_targets('rename')
        self.rename_button.configure(state='normal' if idle and len(chosen) == 1 and accepted else 'disabled')
        self.menu_entry(self.action_menu, 4, label=f'다른 이름으로 저장 ({1 if len(chosen) == 1 and accepted else 0})', state=self.rename_button.cget('state'))
        self.details_button.configure(state='normal' if chosen and not self.closing else 'disabled')
        self.menu_entry(self.action_menu, 5, label=f'문제 자세히 보기 ({len(chosen)})', state=self.details_button.cget('state'))
        self.action_more.configure(state='normal' if chosen and not self.closing else 'disabled')
        self.global_more.configure(state='disabled' if self.closing else 'normal')
        self.menu_entry(self.global_menu, 0, state='disabled' if self.closing else 'normal')
        self.menu_entry(self.global_menu, 1, state='normal' if self.config and not busy and not self.closing else 'disabled')
        self.menu_entry(self.global_menu, 2, state='normal' if any(e.get('last_preserved_copy') for e in self.notes.values()) and not self.closing else 'disabled')
        if not self.failed:
            self.run_label.set({'idle': '대기 중', 'watch': '자동 동기화 중', 'working': '동기화 중', 'stopping': '중지 중'}[mode])
        self.close_hint.set('창을 닫으면 트레이에서 자동 동기화를 계속합니다.' if mode == 'watch' and self.tray and self.tray.visible else
                            '창을 닫으면 진행 중 작업을 마무리한 뒤 종료합니다.' if busy else '창을 닫으면 프로그램이 종료됩니다.')
        if self.initialized and not self.config and not busy and not self.failed and not self.closing:
            self.banner.set('설정에서 삼성노트 백업과 저장 폴더/Vault를 선택하세요.')
        if not busy and self.run_keys:
            self.run_keys.clear()
        if self.tray:
            self.tray.watching = mode == 'watch'
            self.tray.busy = busy
            self.tray.stopping = mode == 'stopping'
        self.update_status()
        self.update_chips()
        self.layout_actions()

    def update_config_summary(self):
        if not self.config:
            self.backup_summary.set('삼성노트 백업: 설정 필요')
            self.vault_summary.set('저장 폴더/Vault: 설정 필요')
            self.folder_summary.set('가져올 폴더: 설정 필요')
            return
        from .folder_selection import summary, selection_for
        self.backup_summary.set('삼성노트 백업: ' + self.config['backup'])
        self.vault_summary.set('저장 폴더/Vault: ' + self.config['vault'])
        self.folder_summary.set('가져올 폴더: ' + summary(selection_for(self.config)))

    def sync_once(self, sources=None):
        if self.controller.busy or not self.paths_saved(): return
        self.failed = False; self.controller.start(only_sources=sources)
        self.banner.set('동기화를 준비하고 있습니다.'); self.controls()

    def queue_image_review(self, value):
        document, groups = value
        pending = {g for g in groups if (document,g) not in self.offered_reviews}
        if not pending:return
        self.pending_reviews.setdefault(document,set()).update(pending)
        if self.controller.busy and self.controller.kind == 'watch' and not self.controller.stop_event.is_set():
            self.resume_review_watch=True
            self.controller.stop()

    def open_image_review(self):
        if self.settings_dialog or self.resolution_view or self.review_dialog:return
        from .image_review_dialog import ImageReviewDialog
        documents=self.pending_reviews;self.pending_reviews={}
        self.offered_reviews.update((d,g) for d,groups in documents.items() for g in groups)
        resume=self.resume_review_watch;self.resume_review_watch=False
        def finish(changed=False):
            if self.closing:return
            if changed or resume:
                self.failed=False;self.controller.start(watch=resume)
                self.banner.set('이미지 검토 결과를 반영하고 있습니다.' if changed else '자동 동기화를 다시 시작합니다.')
            self.controls()
        self.show()
        try:
            self.review_dialog=ImageReviewDialog(self,self.controller.root/'image-review',
                self.config.get('repeat_distance',8),app=self,documents=documents,
                on_apply=lambda:finish(True),on_close=finish)
        except Exception as error:
            self.log_lines.append(f'이미지 검토 창을 열지 못했습니다: {error}')
            self.banner.set('이미지 검토 창을 열지 못했습니다. 설정에서 반복 이미지 그룹 검토를 열어 주세요.')
            if resume:self.controller.start(watch=True)

    def toggle(self):
        if self.closing or self.settings_dialog or self.resolution_view or self.review_dialog: return
        if self.controller.busy:
            if self.controller.kind == 'watch':
                self.controller.stop(); self.banner.set('시작한 변환과 저장을 마친 뒤 자동 동기화를 중지합니다.')
        elif self.config:
            self.failed = False; self.controller.start(watch=True)
            self.banner.set('삼성노트 백업의 변경을 확인하고 있습니다.')
        else: self.edit_settings()
        self.controls()

    def retry(self):
        if self.controller.busy or not self.paths_saved(): return
        accepted, skipped = self.action_targets('retry')
        if accepted:
            self.controller.start(only_sources=accepted)
            if skipped: self.pending_results.extend(skipped)
            self.controls()

    def restore_targets(self):
        return self.action_targets('restore')[0]

    def restore(self):
        from tkinter import messagebox
        accepted, skipped = self.action_targets('restore')
        if self.controller.busy or not self.paths_saved() or not accepted: return
        preview = '\n'.join(self.notes[key]['name'] for key in sorted(accepted)[:8])
        message = (f'선택 {len(self.checked_targets())}개 중 {len(accepted)}개를 현재 삼성노트 백업으로 다시 만들까요?\n\n'
                   + preview + '\n\n남아 있는 Markdown 사용자 영역은 유지합니다. 삭제된 메모나 과거 버전을 복구하는 기능은 아닙니다.\n'
                   '생성 영역이나 PDF가 바뀌었다면 해당 문서는 중단합니다.')
        if skipped: message += f'\n실행 조건에 맞지 않는 {len(skipped)}개는 결과에 제외 이유를 표시합니다.'
        if messagebox.askyesno('저장 폴더/Vault에 다시 저장', display(message), parent=self.window):
            self.failed = False
            self.controller.restore(accepted, skipped) if skipped else self.controller.restore(accepted)
            self.banner.set('현재 백업으로 누락 파일을 다시 만들고 있습니다.'); self.controls()

    def policy(self):
        from tkinter import messagebox
        action = 'resume' if self.group == 'retired' else 'retire'
        accepted, skipped = self.action_targets(action)
        if not accepted or self.controller.busy or not self.paths_saved(): return
        policy = 'managed' if action == 'resume' else 'retired'
        message = ('자동 갱신 대상으로 되돌립니다. 파일을 즉시 만들지는 않습니다. 누락 파일은 별도로 다시 저장하세요.'
                   if action == 'resume' else '남은 파일과 동기화 이력은 유지하며 이 문서의 자동 갱신을 중단합니다.')
        if messagebox.askyesno('관리 재개' if action == 'resume' else '관리 중단',
                              f'선택 {len(self.checked_targets())}개 중 실행 가능 {len(accepted)}개\n\n{message}', parent=self.window):
            self.pending_results.extend(skipped)
            self.controller.policy(accepted, policy); self.controls()

    def resolve(self):
        accepted, skipped = self.action_targets('resolve')
        if accepted and not self.controller.busy and self.paths_saved():
            self.pending_results.extend(skipped)
            self.controller.prepare_resolution(accepted); self.controls()

    def text_area(self, parent, height=8):
        frame = self.ttk.Frame(parent)
        text = NFCText(frame, wrap='word', height=height, width=1, font='SunValleyBodyFont')
        self.theme.refresh(text)
        scroll = self.ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        return frame, text

    def footer(self, parent, *buttons):
        """Dialog footer in the status-bar surface; buttons are (label, command, accent)."""
        sp = self.theme.space
        bar = self.ttk.Frame(parent, style='Status.TFrame', padding=(sp('l'), sp('m')))
        for label, command, accent in reversed(buttons):
            self.ttk.Button(bar, text=label, command=command,
                            style='Accent.TButton' if accent else 'TButton').pack(side='right', padx=(sp('s'), 0))
        return bar

    def copy_text(self, value):
        self.window.clipboard_clear(); self.window.clipboard_append(value)

    def open_path(self, path, folder=False):
        try: os.startfile(str(Path(path).parent if folder else path))
        except OSError as error: self.error(str(error))

    def resolution_dialog(self, offers, skipped):
        if self.closing: return
        skipped = list(skipped) + self.pending_results; self.pending_results = []
        if not offers:
            self.results_dialog(skipped); return
        sp = self.theme.space
        view = self.tk.Toplevel(self.window); self.resolution_view = view
        view.title('Markdown 충돌 해결'); self.size(view, 640, 500, 480, 360)
        view.transient(self.window); view.grab_set(); self.controls()
        view.columnconfigure(0, weight=1); view.rowconfigure(1, weight=1, minsize=35)
        choice = self.tk.StringVar(view, value='preserve-vault')
        intro = (f'{len(offers)}개 문서의 수정 내용을 확인했습니다. 수정본 보관 후 재개를 권장합니다.\n'
                 '작성 영역을 구분할 수 있는 문서는 사용자 메모를 유지합니다.\n'
                 '구형 문서나 경계가 손상된 문서는 전체를 보관한 뒤 필요한 메모를 새 파일로 직접 옮겨야 합니다.')
        intro_frame, intro_text = self.text_area(view, height=4)
        intro_frame.grid(row=0, column=0, sticky='ew', padx=sp('l'), pady=(sp('l'), sp('s')))
        intro_text.insert('end', intro + '\n삼성노트 버전 사용은 생성 영역의 수정을 버립니다.'); intro_text.configure(state='disabled')
        frame, text = self.text_area(view, height=6); frame.grid(row=1, column=0, sticky='nsew', padx=sp('l'))
        for key, request in offers.items():
            text.insert('end', f'{self.notes.get(key, {}).get("name", key)}: 누락 {", ".join(sorted(request["missing"])) or "없음"}\n')
        for result in skipped:
            text.insert('end', f'제외: {self.notes.get(result["source"], {}).get("name", result["source"])}\n{result.get("user_message", "실행 조건을 확인하세요.")}\n')
        text.configure(state='disabled')
        self.ttk.Radiobutton(view, text='Vault 수정본 보관 후 재개 (권장)', variable=choice, value='preserve-vault').grid(row=2, column=0, sticky='w', padx=sp('l'), pady=(sp('m'), sp('xs')))
        self.ttk.Radiobutton(view, text='삼성노트 버전 사용', variable=choice, value='use-source',
                             state='normal' if all(r['use_source'] for r in offers.values()) else 'disabled').grid(row=3, column=0, sticky='w', padx=sp('l'), pady=(0, sp('m')))
        def close():
            self.resolution_view = None; view.destroy(); self.controls()
        def apply():
            if self.closing: return
            requests = {key: dict(request, choice=choice.get()) for key, request in offers.items()}
            close(); self.controller.resolve(requests, skipped); self.controls()
        view.protocol('WM_DELETE_WINDOW', close)
        label = '선택 적용 및 누락 파일 재생성 동의' if any(r['missing'] for r in offers.values()) else '선택 적용'
        self.footer(view, ('나중에 결정', close, False), (label, apply, True)).grid(row=4, column=0, sticky='ew')

    def results_dialog(self, results):
        if self.closing: return
        sp = self.theme.space
        view = self.tk.Toplevel(self.window); view.title('문서 처리 결과'); self.size(view, 680, 500, 480, 360)
        view.columnconfigure(0, weight=1); view.rowconfigure(1, weight=1)
        successful = {'synced', 'managed', 'retired', '관리 재개', '관리 중단'}
        failures = {'error', 'failed', 'conflict'}
        done = sum(r['status'] in successful for r in results); errors = sum(r['status'] in failures for r in results)
        self.ttk.Label(view, text=f'완료 {done}개, 보류 {len(results)-done-errors}개, 실패 또는 확인 필요 {errors}개',
                       style='Section.TLabel').grid(row=0, column=0, sticky='w', padx=sp('l'), pady=(sp('l'), sp('s')))
        frame = self.ttk.Frame(view); frame.grid(row=1, column=0, sticky='nsew', padx=sp('l'))
        tree = self.ttk.Treeview(frame, columns=('name', 'result'), show='headings', selectmode='browse')
        for col, label in (('name', '문서'), ('result', '결과')):
            tree.heading(col, text=label, anchor='w'); tree.column(col, width=220, minwidth=80)
        scroll = self.ttk.Scrollbar(frame, command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        for i, result in enumerate(results):
            tree.insert('', 'end', iid=str(i), values=(display(self.notes.get(result['source'], {}).get('name', result['source'])), display(STATUS.get(result['status'], result['status']))))
        message_frame, message = self.text_area(view, height=3); message_frame.grid(row=2, column=0, sticky='ew', padx=sp('l'), pady=sp('s'))
        buttons = self.ttk.Frame(view); buttons.grid(row=3, column=0, sticky='ew', padx=sp('l'))
        def current():
            return results[int(tree.selection()[0])] if tree.selection() else {}
        for i, (label, action) in enumerate((('보존본 열기', lambda: self.open_path(current()['copy'])),
                                             ('파일 위치 열기', lambda: self.open_path(current()['copy'], True)),
                                             ('경로 복사', lambda: self.copy_text(current()['copy'])))):
            button = self.ttk.Button(buttons, text=label, command=action, state='disabled')
            button.grid(row=0, column=i, sticky='w', padx=(0, sp('xs')))
        technical, raw = self.text_area(view, height=5)
        def toggle_technical():
            if technical.winfo_manager(): technical.grid_remove()
            else: technical.grid(row=5, column=0, sticky='ew', padx=sp('l'), pady=sp('s'))
        self.ttk.Button(view, text='기술 정보 펼치기/접기', command=toggle_technical).grid(row=4, column=0, sticky='w', padx=sp('l'), pady=sp('s'))
        self.footer(view, ('닫기', view.destroy, False)).grid(row=6, column=0, sticky='ew')
        def selected(event=None):
            result = current()
            message.configure(state='normal'); message.delete('1.0', 'end')
            if result.get('user_message'):
                text = result['user_message']
            elif result.get('status') in ('retired', '관리 중단'):
                text = '자동 관리를 중단했습니다. 남은 파일과 동기화 이력은 유지합니다.'
            elif result.get('status') in ('managed', '관리 재개'):
                text = '다시 자동 갱신 대상이 되었습니다. 누락 파일은 별도로 다시 저장해야 합니다.'
            elif result.get('status') in successful:
                text = '처리가 완료되었습니다.'
            elif result.get('source') in self.notes:
                key = result['source']; text = guidance(self.notes[key], self.observation(key))
            else:
                text = '처리를 완료하지 못했거나 보류했습니다. 기술 정보에서 원문 오류를 확인한 뒤 다시 시도하세요.'
            message.insert('end', text); message.configure(state='disabled')
            raw.configure(state='normal'); raw.delete('1.0', 'end')
            raw.insert('end', f'{result.get("source", "")}\n{result.get("message", "")}\n보존본: {result.get("copy") or "없음"}'); raw.configure(state='disabled')
            for button in buttons.winfo_children(): button.configure(state='normal' if result.get('copy') else 'disabled')
        tree.bind('<<TreeviewSelect>>', selected)
        if results: tree.selection_set('0')
        self.last_results_view = view

    def rename(self):
        key = self.single_target()
        accepted, _ = self.action_targets('rename')
        if not key or key not in accepted or self.controller.busy or not self.paths_saved(): return
        name = ask_name(self.window, self.notes[key]['name'])
        if name and display(name) != display(self.notes[key]['name']):
            self.failed = False; self.controller.launch(lambda: self.controller.rename(key, name), kind='sync')
            self.banner.set('새 이름으로 저장할 준비를 하고 있습니다.')
        self.controls()

    def open_vault(self):
        if self.config: self.open_path(self.config['vault'])

    def details(self, global_view=False):
        if self.closing: return
        sp = self.theme.space
        view = self.tk.Toplevel(self.window); view.title('진단 및 로그' if global_view else '문제 자세히 보기'); self.size(view, 680, 520, 480, 360)
        keys = [] if global_view else sorted(self.checked_targets())
        frame, text = self.text_area(view, height=9); frame.pack(fill='both', expand=True, padx=sp('l'), pady=(sp('l'), sp('s')))
        for key in keys:
            text.insert('end', f'{self.notes[key]["name"]}\n{guidance(self.notes[key], self.observation(key))}\n\n')
        if not keys: text.insert('end', f'{self.banner.get()}\n{self.status_summary.get()}\n{self.result_count.get()}\n{self.close_hint.get()}\n전체 기록과 경로는 아래 기술 정보에서 확인하세요.')
        text.configure(state='disabled')
        technical, raw = self.text_area(view, height=8)
        raw_parts = []
        for key in keys:
            raw_parts.append(f'{key}\n{self.notes[key].get("error") or ""}\n{self.observation(key)}\n최근 보존본: {self.notes[key].get("last_preserved_copy")}\n\n')
        if self.config:
            raw_parts.append(f'삼성노트 백업: {self.config["backup"]}\n저장 폴더/Vault: {self.config["vault"]}\n\n')
        raw_parts.append('\n'.join(self.log_lines) + f'\n\n로그 파일: {self.controller.root/"desktop.log"}')
        raw_technical = ''.join(raw_parts)
        raw.insert('end', raw_technical)
        raw.configure(state='disabled')
        footer = self.footer(view, ('닫기', view.destroy, False))
        footer.pack(side='bottom', fill='x')
        tools = self.ttk.Frame(view); tools.pack(side='bottom', fill='x', padx=sp('l'), pady=(sp('xs'), sp('m')))
        if global_view: technical.pack(fill='both', expand=True, padx=sp('l'), pady=sp('xs'))
        def toggle():
            if technical.winfo_manager(): technical.pack_forget()
            else: technical.pack(fill='both', expand=True, padx=sp('l'), pady=sp('xs'), before=tools)
        self.ttk.Button(view, text='기술 정보 펼치기/접기', command=toggle).pack(anchor='w', padx=sp('l'), before=tools)
        self.ttk.Button(tools, text='기술 정보 복사', command=lambda: self.copy_text(raw_technical)).grid(row=0, column=0, sticky='w')
        self.ttk.Button(tools, text='백업 내 경로 복사', command=lambda: self.copy_text('\n'.join(keys)), state='normal' if keys else 'disabled').grid(row=0, column=1, sticky='w', padx=(sp('xs'), 0))
        self.ttk.Button(tools, text='경로 진단', command=lambda: self.diagnose_paths(keys), state='normal' if self.config and not self.controller.busy else 'disabled').grid(row=0, column=2, sticky='w', padx=(sp('xs'), 0))
        copies = [{'source': key, 'status': 'synced', 'copy': self.notes[key]['last_preserved_copy']} for key in (self.notes if global_view else keys) if self.notes[key].get('last_preserved_copy')]
        self.ttk.Button(tools, text='최근 보존본 보기', command=lambda: self.results_dialog(copies), state='normal' if copies else 'disabled').grid(row=0, column=3, sticky='w', padx=(sp('xs'), 0))

    def diagnose_paths(self, keys):
        from tkinter import messagebox
        if self.controller.busy or not self.config or self.closing: return
        message = ('환경 버전, 경로 정규화 여부, 존재 및 접근 결과와 실패 단계를 확인합니다.\n'
                   '선택 문서는 임시 사본을 만들어 디코딩까지 검사하며 원본과 저장 파일은 변경하지 않습니다.\n'
                   '기본 결과에는 경로, 문서명, 코드포인트를 넣지 않습니다. 원문은 별도로 선택해 복사할 수 있습니다.\n\n실행할까요?')
        if messagebox.askyesno('경로 진단', message, parent=self.window):
            self.controller.diagnose_paths(keys, dict(self.selected_paths), self.window.tk.call('info', 'patchlevel'))
            self.controls()

    def diagnostics_dialog(self, report):
        if self.closing: return
        sp = self.theme.space
        view = self.tk.Toplevel(self.window); view.title('경로 진단 결과'); self.size(view, 640, 480, 480, 360)
        frame, text = self.text_area(view); frame.pack(fill='both', expand=True, padx=sp('l'), pady=(sp('l'), sp('s')))
        basic = json.dumps({key: value for key, value in report.items() if key != 'raw'}, ensure_ascii=False, indent=2)
        text.insert('end', basic); text.configure(state='disabled')
        def copy_raw():
            from tkinter import messagebox
            if messagebox.askyesno('원문 경로 포함', '개인 경로와 문서명이 포함됩니다. 원문도 복사할까요?', parent=view):
                self.copy_text(json.dumps(report, ensure_ascii=False, indent=2))
        self.footer(view, ('기본 결과 복사 (경로 제외)', lambda: self.copy_text(basic), False),
                    ('원문 경로 포함 복사…', copy_raw, False), ('닫기', view.destroy, False)).pack(side='bottom', fill='x')

    def update_state(self, state):
        from datetime import datetime
        focus, scroll = self.tree.focus(), self.tree.yview()
        self.notes = state['notes']; self.observations = state.get('observations', {})
        counts = {key: 0 for key in GROUPS}; visible = []; self.status_texts = {}
        chips = {'확인 필요': 0, '실패': 0}
        for key, entry in self.notes.items():
            item = self.observation(key); category = group(entry, item)
            if item.get('excluded') and category != 'retired': continue
            counts[category] += 1
            if category == self.group and self.group != 'retired':
                if failed(entry): chips['실패'] += 1
                elif needs_attention(entry, item): chips['확인 필요'] += 1
            if category == self.group and matches(key, entry, item, self.search.get(), self.state_filter.get()): visible.append(key)
        self.chip_counts = chips
        self.checked.intersection_update(visible)
        now = datetime.now().astimezone()
        self.selecting = True
        try:
            visible_set = set(visible)
            for iid in self.tree.get_children():
                if iid not in visible_set: self.tree.delete(iid)
            for index, key in enumerate(visible):
                entry = self.notes[key]
                item = self.observation(key)
                attention = self.group != 'retired' and needs_attention(entry, item)
                # The status cell is drawn as an icon; its words live in the tooltip only.
                self.status_texts[key] = display(status_text(entry, item))
                values = ('☑' if key in self.checked else '☐', display('/'.join(p for p in (entry.get('folder', ''), entry['name']) if p)),
                          '', format_time(synced_at(entry), now))
                tags = ('attention',) if attention else ()
                if self.tree.exists(key): self.tree.item(key, values=values, tags=tags); self.tree.move(key, '', index)
                else: self.tree.insert('', 'end', iid=key, values=values, tags=tags)
            self.tree_keys = {key: key for key in visible}
            if focus in visible_set: self.tree.focus(focus)
            elif visible: self.tree.focus(visible[0])
            if scroll: self.tree.yview_moveto(scroll[0])
            self.group_counts = counts; self.group_labels()
            self.result_count.set(f'검색 결과 {len(visible)}개')
            self.summary = document_summary(self.notes, self.observations)
            self.track_run()
            self.refresh_checks()
        finally: self.selecting = False
        self.update_empty(visible, counts)
        if not self.failed and not self.closing:
            self.banner.set(self.issue_summary())
        self.status_cells.queue()

    def track_run(self):
        """Progress and current documents from note states; the engine is unchanged."""
        from datetime import datetime
        pending = {key for key, entry in self.notes.items() if entry.get('status') in PENDING}
        if self.controller.busy and pending:
            self.run_keys |= pending
        elif self.run_keys and not pending:
            self.last_sync = datetime.now().astimezone(); self.run_keys = set()
        self.working_names = [display(entry['name']) for entry in self.notes.values() if entry.get('status') in WORKING]
        self.latest_success = max((t for t in map(synced_at, self.notes.values()) if t), default=None)

    def update_empty(self, visible, counts):
        if visible:
            self.empty_label.place_forget(); return
        if not self.config:
            text = '설정에서 삼성노트 백업과 저장 폴더를 선택하면\n문서가 여기에 표시됩니다.'
        elif counts.get(self.group):
            text = '조건에 맞는 문서가 없습니다.'
        else:
            text = f'{GROUPS[self.group]}에 해당하는 문서가 없습니다.'
        self.empty_text.set(text)
        self.empty_label.place(relx=.5, rely=.5, anchor='center')

    def issue_summary(self):
        if self.summary['unavailable']:
            return '백업/Vault 접근 미확인. 설정에서 경로와 연결 상태를 확인하세요.'
        return ''

    def show(self):
        if self.closing: return
        self.window.deiconify(); self.window.lift(); self.window.focus_force()

    def on_close(self):
        action = close_action(self.controller.busy, self.controller.kind, self.controller.stop_event.is_set(), bool(self.tray and self.tray.visible))
        if action == 'hide': self.window.withdraw()
        elif action == 'confirm':
            from tkinter import messagebox
            if messagebox.askyesno('자동 동기화 종료', '트레이를 사용할 수 없습니다. 진행 중 작업을 마친 뒤 종료할까요?', parent=self.window): self.exit()
        else: self.exit()

    def hide(self):
        self.on_close()

    def error(self, message):
        self.failed = True; self.log_lines.append(message); self.log_lines = self.log_lines[-100:]
        if self.closing: return
        self.banner.set('작업을 완료하지 못했습니다. 메뉴의 진단 및 로그에서 확인하세요.')
        if self.settings_dialog:
            self.settings_dialog.saving = False; self.settings_dialog.set_enabled(True)
            self.settings_dialog.message.set('설정을 저장하지 못했습니다. 기술 정보에서 오류를 확인하고 다시 시도하세요.')
        self.show()

    def exit(self, session_end=False):
        if self.closing: return
        if self.settings_dialog:
            if not self.settings_dialog.close(force=session_end or self.controller.busy): return
        self.closing = True; self.startup = False; self.pending_offer = None; self.pending_results = []
        if self.review_dialog:self.review_dialog.close()
        if self.resolution_view:
            self.resolution_view.destroy(); self.resolution_view = None
        self.controller.stop(); self.banner.set('진행 중인 작업을 마친 뒤 종료합니다.'); self.controls()

    def startup_watch(self):
        if not self.closing and self.startup and self.initialized and not self.controller.busy and not self.failed and self.config and self.config.get('start_on_login'):
            self.toggle()

    def poll(self):
        try:
            while True:
                kind, value = self.controller.events.get_nowait()
                if kind == 'config':
                    from .sync_config import pair_key
                    if self.config is None or pair_key(self.config) != pair_key(value):
                        self.checked.clear();self.pending_reviews.clear();self.offered_reviews.clear()
                    self.config = value; self.update_config_summary()
                    if self.settings_dialog and self.settings_dialog.saving: self.settings_dialog.close(force=True)
                elif kind == 'reset':
                    self.pending_reviews.clear();self.offered_reviews.clear();self.resume_review_watch=False
                    self.config=None;self.checked.clear();self.pending_offer=None;self.pending_results=[]
                    self.run_keys.clear();self.last_sync=self.latest_success=None
                    if self.settings_dialog:self.settings_dialog.close(force=True)
                    self.update_config_summary();self.update_state({'notes':{}})
                    self.banner.set('초기화했습니다. 새 동기화를 설정하세요.')
                    if not self.closing:self.window.after(100,self.edit_settings)
                elif kind == 'locations': self.locations = value
                elif kind == 'state': self.update_state(value)
                elif kind == 'resolution_offer':
                    if not self.closing: self.pending_offer = value
                elif kind == 'results':
                    if not self.closing: self.pending_results.extend(value)
                elif kind == 'image_review':
                    if not self.closing:self.queue_image_review(value)
                elif kind == 'diagnostics':
                    if not self.closing: self.diagnostics_dialog(value)
                elif kind == 'log':
                    self.log_lines.append(value); self.log_lines = self.log_lines[-100:]
                elif kind in ('error', 'tray_error'): self.error(value)
                elif kind == 'show': self.show()
                elif kind == 'toggle': self.toggle()
                elif kind in ('exit', 'session_end'): self.exit(session_end=kind == 'session_end')
                elif kind == 'initialized': self.initialized = True
                elif kind == 'finished':
                    if value in ('sync', 'setup', 'watch') and not self.failed:
                        from datetime import datetime
                        self.last_sync = datetime.now().astimezone()
                    if value == 'initialize':
                        self.initialized = True
                        if not self.closing:
                            if self.startup: self.window.after(100, self.startup_watch)
                            if not self.config: self.window.after(100, self.edit_settings)
                            elif self.startup and (not self.config.get('start_on_login') or self.failed): self.show()
                    if value == 'watch' and not self.closing:
                        self.show()
        except Empty: pass
        self.controls()
        if not self.controller.busy and not self.closing:
            if self.pending_offer is not None:
                offers, skipped = self.pending_offer; self.pending_offer = None
                self.resolution_dialog(offers, skipped)
            elif self.pending_results:
                results, self.pending_results = self.pending_results, []
                self.results_dialog(results)
            elif self.pending_reviews:
                self.open_image_review()
        if self.closing and not self.controller.busy:
            if self.tray: self.tray.close()
            self.controller.close(); self.window.destroy(); return
        self.window.after(100, self.poll)

def main(argv=None):
    from multiprocessing import freeze_support
    freeze_support()
    parser = argparse.ArgumentParser(description='SNo2md Windows desktop')
    parser.add_argument('--startup', action='store_true', help='Start hidden and resume configured watching')
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Windows 11 x64 desktop only; use sno2md CLI on this platform')
    import tkinter as tk
    from tkinter import messagebox
    from . import windows_desktop as native
    from .gui_layout import enable_dpi_awareness
    # Draw at the monitor's real DPI instead of letting Windows stretch a 96 DPI bitmap.
    dpi_aware = enable_dpi_awareness()
    window = tk.Tk()
    window.withdraw()
    if dpi_aware:
        window.tk.call('tk', 'scaling', window_dpi(window)/72)
    controller = DesktopController(state_directory())
    tray = None
    try:
        try:
            tray = native.WindowsTray(lambda kind, value=None: controller.events.put((kind, value)))
        except (ImportError, OSError) as error:
            controller.events.put(('tray_error', str(error)))
        if tray and not tray.is_owner:
            if not args.startup and not tray.activate_existing():
                messagebox.showinfo('SNo2md', '이미 실행 중입니다. 트레이에서 열어 주세요.', parent=window)
            tray.close()
            controller.close()
            window.destroy()
            return
        if tray:
            try:
                tray.start()
            except OSError as error:
                controller.events.put(('tray_error', str(error)))
        Application(window, controller, native, tray, startup=args.startup, dpi_aware=dpi_aware)
        if not args.startup or not tray or not tray.visible: window.deiconify()
        window.mainloop()
    except Exception as error:
        messagebox.showerror('SNo2md 실행 오류', display(error), parent=window)
        controller.stop()
        if controller.thread: controller.thread.join()
        if tray: tray.close()
        controller.close()
        window.destroy()
        raise


if __name__ == '__main__':
    main()
