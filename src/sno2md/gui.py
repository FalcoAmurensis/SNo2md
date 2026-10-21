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
from .gui_model import (STATUS, GROUPS, FILTERS, execution, close_action,
                        matches, targets, guidance, status_text)


class Application:
    def __init__(self, window, controller, native, tray=None, startup=False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.window, self.controller, self.native, self.tray = window, controller, native, tray
        self.startup = startup
        self.config = None
        self.notes, self.observations, self.tree_keys = {}, {}, {}
        self.checked = set()
        self.anchor = None
        self.selecting = False
        self.group = 'active'
        self.settings_dialog = self.resolution_view = None
        self.pending_offer = None
        self.pending_results = []
        self.locations = {'backup': [], 'vault': []}
        self.selected_paths = {}
        self.closing = self.initialized = self.failed = False
        self.log_lines = []
        self.search = tk.StringVar()
        self.state_filter = tk.StringVar(value='전체')
        self.banner = tk.StringVar(value='설정을 불러오는 중…')
        self.run_label = tk.StringVar(value='작업 중')
        self.close_hint = tk.StringVar()
        self.selection_summary = tk.StringVar(value='선택 0개')
        self.result_count = tk.StringVar()
        self.backup_summary = tk.StringVar(value='삼성노트 백업: 설정이 필요합니다.')
        self.vault_summary = tk.StringVar(value='저장 폴더/Vault: 설정이 필요합니다.')
        self.folder_summary = tk.StringVar()
        window.title('SNo2md — 삼성노트 동기화')
        window.geometry('1000x650'); window.minsize(700, 480)
        style = ttk.Style(window); style.theme_use('clam')
        style.configure('.', font=('맑은 고딕', 10))
        style.configure('TButton', padding=(7, 4))
        style.configure('Treeview', rowheight=max(28, int(float(window.tk.call('tk', 'scaling'))*19)))
        style.configure('Title.TLabel', font=('맑은 고딕', 15, 'bold'))
        self.canvas = canvas = tk.Canvas(window, highlightthickness=0, width=1)
        outer_scroll = ttk.Scrollbar(window, command=canvas.yview)
        canvas.configure(yscrollcommand=outer_scroll.set)
        outer_scroll.pack(side='right', fill='y'); canvas.pack(fill='both', expand=True)
        self.body = body = ttk.Frame(canvas, padding=12)
        content = canvas.create_window((0, 0), window=body, anchor='nw')
        self.canvas_content = content
        body.columnconfigure(0, weight=1); body.rowconfigure(5, weight=1, minsize=100)
        def resize(event=None):
            canvas.itemconfigure(content, width=canvas.winfo_width(), height=max(canvas.winfo_height(), body.winfo_reqheight()))
            canvas.configure(scrollregion=canvas.bbox('all'))
        canvas.bind('<Configure>', resize); body.bind('<Configure>', resize)
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta/120), 'units'))
        header = ttk.Frame(body); header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Samsung Notes → Obsidian', style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(header, textvariable=self.run_label).grid(row=0, column=1, sticky='e')
        summary = ttk.Frame(body); summary.grid(row=1, column=0, sticky='ew', pady=8)
        summary.columnconfigure(0, weight=1)
        for row, variable in enumerate((self.backup_summary, self.vault_summary, self.folder_summary)):
            ttk.Label(summary, textvariable=variable, width=1, anchor='w').grid(row=row, column=0, sticky='ew')
        self.settings_button = ttk.Button(summary, text='설정 변경', command=self.edit_settings)
        self.settings_button.grid(row=0, column=1, rowspan=3, padx=(8, 0))
        actions = ttk.Frame(body); actions.grid(row=2, column=0, sticky='ew')
        self.once_button = ttk.Button(actions, text='지금 동기화', command=self.sync_once)
        self.watch_button = ttk.Button(actions, text='자동 동기화 시작', command=self.toggle)
        self.open_button = ttk.Button(actions, text='저장 폴더/Vault 열기', command=self.open_vault)
        self.run_buttons = (self.once_button, self.watch_button, self.open_button)
        for col, button in enumerate(self.run_buttons):
            actions.columnconfigure(col, weight=1); button.grid(row=0, column=col, sticky='ew', padx=(0, 4))
        ttk.Label(body, text='자동 동기화는 삼성노트 백업 폴더의 변경을 확인합니다.', width=1).grid(row=3, column=0, sticky='ew', pady=(4, 7))
        filter_area = ttk.Frame(body); filter_area.grid(row=4, column=0, sticky='ew')
        filter_area.columnconfigure(1, weight=1)
        ttk.Label(filter_area, text='검색').grid(row=0, column=0, padx=(0, 6))
        self.search_box = ttk.Entry(filter_area, textvariable=self.search, width=1)
        self.search_box.grid(row=0, column=1, sticky='ew')
        self.filter_box = ttk.Combobox(filter_area, textvariable=self.state_filter, values=FILTERS, state='readonly', width=12)
        self.filter_box.grid(row=0, column=2, padx=(6, 0))
        self.groups_frame = ttk.Frame(filter_area); self.groups_frame.grid(row=1, column=0, columnspan=3, sticky='ew', pady=6)
        self.group_buttons = {}
        for index, (key, label) in enumerate(GROUPS.items()):
            button = ttk.Button(self.groups_frame, text=label, command=lambda key=key: self.change_group(key))
            button.grid(row=index//2, column=index%2, sticky='ew', padx=(0, 4), pady=2)
            self.group_buttons[key] = button
        self.groups_frame.columnconfigure(0, weight=1); self.groups_frame.columnconfigure(1, weight=1)
        ttk.Label(filter_area, textvariable=self.result_count).grid(row=2, column=0, columnspan=3, sticky='w')
        table = ttk.Frame(body); table.grid(row=5, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1); table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('check', 'name', 'status'), show='headings', selectmode='extended', height=5)
        self.tree.heading('check', text='☐', command=self.toggle_all_checked)
        self.tree.column('check', width=38, minwidth=38, stretch=False, anchor='center')
        for key, label, width in (('name', '문서명', 320), ('status', '상태', 260)):
            self.tree.heading(key, text=label); self.tree.column(key, width=width, minwidth=100)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, command=self.tree.yview); scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Button-1>', self.click_check)
        self.tree.bind('<space>', self.space_check)
        self.tree.bind('<Control-a>', self.select_all)
        self.tree.bind('<Control-A>', self.select_all)
        self.tree.bind('<<TreeviewSelect>>', self.selection_changed)
        footer = ttk.Frame(body); footer.grid(row=6, column=0, sticky='ew', pady=(6, 0))
        ttk.Label(footer, textvariable=self.selection_summary).grid(row=0, column=0, sticky='w')
        self.action_frame = ttk.Frame(footer); self.action_frame.grid(row=1, column=0, sticky='ew'); footer.columnconfigure(0, weight=1)
        self.retry_button = ttk.Button(self.action_frame, command=self.retry)
        self.restore_button = ttk.Button(self.action_frame, command=self.restore)
        self.policy_button = ttk.Button(self.action_frame, command=self.policy)
        self.resolve_button = ttk.Button(self.action_frame, command=self.resolve)
        self.rename_button = ttk.Button(self.action_frame, text='다른 이름으로 저장', command=self.rename)
        self.details_button = ttk.Button(self.action_frame, text='문제 자세히 보기', command=self.details)
        self.document_buttons = (self.retry_button, self.restore_button, self.policy_button, self.resolve_button, self.rename_button, self.details_button)
        self.action_frame.bind('<Configure>', self.layout_actions)
        self.groups_frame.bind('<Configure>', lambda e: self.group_labels())
        ttk.Label(body, textvariable=self.banner, width=1).grid(row=7, column=0, sticky='ew', pady=(5, 0))
        ttk.Label(body, textvariable=self.close_hint, width=1).grid(row=8, column=0, sticky='ew', pady=(3, 0))
        window.protocol('WM_DELETE_WINDOW', self.on_close)
        window.report_callback_exception = lambda kind, value, tb: self.error(f'{kind.__name__}: {value}')
        self.search.trace_add('write', lambda *_: self.filter_changed())
        self.state_filter.trace_add('write', lambda *_: self.filter_changed())
        self.controls(); window.after(100, self.poll)
        controller.launch(controller.initialize, kind='initialize')

    def edit_settings(self):
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
        narrow = self.groups_frame.winfo_width() < 900
        for key, button in self.group_buttons.items():
            label = GROUPS[key]
            if narrow:
                label = label.replace('백업에서 찾을', '백업에서\n찾을').replace('Vault 파일', 'Vault\n파일')
            button.configure(text=f'{label} ({counts.get(key, 0)})')

    def layout_actions(self, event=None):
        width = self.action_frame.winfo_width()
        columns = 3
        while columns > 1:
            sizes = [max(b.winfo_reqwidth() for b in self.document_buttons[col::columns])+6 for col in range(columns)]
            if sum(sizes) <= width: break
            columns -= 1
        for col in range(3): self.action_frame.columnconfigure(col, weight=1 if col < columns else 0)
        for i, button in enumerate(self.document_buttons):
            button.grid(row=i//columns, column=i%columns, sticky='ew', padx=(0, 4), pady=2)
        self.window.after_idle(self.fit_content)

    def fit_content(self):
        if self.closing: return
        self.canvas.itemconfigure(self.canvas_content, width=self.canvas.winfo_width(),
                                  height=max(self.canvas.winfo_height(), self.body.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def controls(self):
        busy = self.controller.busy
        mode = execution(busy, self.controller.kind, self.controller.stop_event.is_set())
        idle = self.initialized and not busy and not self.closing and not self.settings_dialog and not self.resolution_view
        self.settings_button.configure(state='normal' if self.initialized and not busy and not self.closing and not self.resolution_view else 'disabled')
        self.once_button.configure(state='normal' if idle and self.config else 'disabled')
        self.open_button.configure(state='normal' if self.config and not self.closing else 'disabled')
        self.watch_button.configure(text='중지 중…' if mode == 'stopping' else '자동 동기화 중지' if mode == 'watch' else '자동 동기화 시작',
                                    state='normal' if not self.closing and (mode == 'watch' or idle and self.config) else 'disabled')
        chosen = self.checked_targets()
        self.selection_summary.set(f'선택 {len(chosen)}개 · 작업 이름 옆 숫자는 실행 가능한 문서 수입니다.')
        policy = 'resume' if self.group == 'retired' else 'retire'
        for button, action, label in ((self.retry_button, 'retry', '다시 시도'),
                                      (self.restore_button, 'restore', '저장 폴더/Vault에 다시 저장'),
                                      (self.policy_button, policy, '관리 재개' if policy == 'resume' else '관리 중단'),
                                      (self.resolve_button, 'resolve', 'Markdown 충돌 해결')):
            accepted, _ = self.action_targets(action)
            button.configure(text=f'{label} ({len(accepted)})', state='normal' if idle and accepted else 'disabled')
        accepted, _ = self.action_targets('rename')
        self.rename_button.configure(state='normal' if idle and len(chosen) == 1 and accepted else 'disabled')
        self.details_button.configure(state='normal' if not self.closing else 'disabled')
        self.run_label.set({'idle': '대기 중', 'watch': '자동 동기화 중', 'working': '작업 중', 'stopping': '중지 중'}[mode])
        self.close_hint.set('창을 닫으면 트레이에서 자동 동기화를 계속합니다.' if mode == 'watch' and self.tray and self.tray.visible else
                            '창을 닫으면 진행 중 작업을 마무리한 뒤 종료합니다.' if busy else '창을 닫으면 프로그램이 종료됩니다.')
        if self.tray:
            self.tray.watching = mode == 'watch'
            self.tray.busy = busy
            self.tray.stopping = mode == 'stopping'
        self.layout_actions()

    def update_config_summary(self):
        if not self.config: return
        from .folder_selection import summary, selection_for
        self.backup_summary.set('삼성노트 백업: ' + self.config['backup'])
        self.vault_summary.set('저장 폴더/Vault: ' + self.config['vault'])
        self.folder_summary.set('가져올 폴더: ' + summary(selection_for(self.config)))

    def sync_once(self, sources=None):
        if self.controller.busy or not self.paths_saved(): return
        self.failed = False; self.controller.start(only_sources=sources)
        self.banner.set('동기화를 준비하고 있습니다.'); self.controls()

    def toggle(self):
        if self.closing or self.settings_dialog or self.resolution_view: return
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
        if messagebox.askyesno('저장 폴더/Vault에 다시 저장', message, parent=self.window):
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
                              f'선택 {len(self.checked_targets())}개 · 실행 가능 {len(accepted)}개\n\n{message}', parent=self.window):
            self.pending_results.extend(skipped)
            self.controller.policy(accepted, policy); self.controls()

    def resolve(self):
        accepted, skipped = self.action_targets('resolve')
        if accepted and not self.controller.busy and self.paths_saved():
            self.pending_results.extend(skipped)
            self.controller.prepare_resolution(accepted); self.controls()

    def text_area(self, parent, height=8):
        frame = self.ttk.Frame(parent)
        text = self.tk.Text(frame, wrap='word', height=height, width=1, font=('맑은 고딕', 10))
        scroll = self.ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        return frame, text

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
        view = self.tk.Toplevel(self.window); self.resolution_view = view
        view.title('Markdown 충돌 해결'); view.geometry('640x500'); view.minsize(480, 360)
        view.transient(self.window); view.grab_set(); self.controls()
        view.columnconfigure(0, weight=1); view.rowconfigure(1, weight=1, minsize=35)
        choice = self.tk.StringVar(value='preserve-vault')
        intro = (f'{len(offers)}개 문서의 수정 내용을 확인했습니다. 수정본 보관 후 재개를 권장합니다.\n'
                 '작성 영역을 구분할 수 있는 문서는 사용자 메모를 유지합니다.\n'
                 '구형 문서나 경계가 손상된 문서는 전체를 보관한 뒤 필요한 메모를 새 파일로 직접 옮겨야 합니다.')
        intro_frame, intro_text = self.text_area(view, height=4)
        intro_frame.grid(row=0, column=0, sticky='ew', padx=12, pady=8)
        intro_text.insert('end', intro + '\n삼성노트 버전 사용은 생성 영역의 수정을 버립니다.'); intro_text.configure(state='disabled')
        frame, text = self.text_area(view, height=6); frame.grid(row=1, column=0, sticky='nsew', padx=12)
        for key, request in offers.items():
            text.insert('end', f'{self.notes.get(key, {}).get("name", key)} · 누락 {", ".join(sorted(request["missing"])) or "없음"}\n')
        for result in skipped:
            text.insert('end', f'제외: {self.notes.get(result["source"], {}).get("name", result["source"])}\n{result.get("user_message", "실행 조건을 확인하세요.")}\n')
        text.configure(state='disabled')
        self.ttk.Radiobutton(view, text='Vault 수정본 보관 후 재개 (권장)', variable=choice, value='preserve-vault').grid(row=2, column=0, sticky='w', padx=12, pady=5)
        self.ttk.Radiobutton(view, text='삼성노트 버전 사용', variable=choice, value='use-source',
                             state='normal' if all(r['use_source'] for r in offers.values()) else 'disabled').grid(row=3, column=0, sticky='w', padx=12)
        def close():
            self.resolution_view = None; view.destroy(); self.controls()
        def apply():
            if self.closing: return
            requests = {key: dict(request, choice=choice.get()) for key, request in offers.items()}
            close(); self.controller.resolve(requests, skipped); self.controls()
        view.protocol('WM_DELETE_WINDOW', close)
        label = '선택 적용 및 누락 파일 재생성 동의' if any(r['missing'] for r in offers.values()) else '선택 적용'
        self.ttk.Button(view, text=label, command=apply).grid(row=4, column=0, sticky='ew', padx=12, pady=8)
        self.ttk.Button(view, text='나중에 결정', command=close).grid(row=5, column=0, pady=(0, 10))

    def results_dialog(self, results):
        if self.closing: return
        view = self.tk.Toplevel(self.window); view.title('문서 처리 결과'); view.geometry('680x500'); view.minsize(480, 360)
        view.columnconfigure(0, weight=1); view.rowconfigure(1, weight=1)
        successful = {'synced', 'managed', 'retired', '관리 재개', '관리 중단'}
        failed = {'error', 'failed', 'conflict'}
        done = sum(r['status'] in successful for r in results); errors = sum(r['status'] in failed for r in results)
        self.ttk.Label(view, text=f'완료 {done}개 · 보류 {len(results)-done-errors}개 · 실패/확인 필요 {errors}개').grid(row=0, column=0, sticky='w', padx=12, pady=10)
        frame = self.ttk.Frame(view); frame.grid(row=1, column=0, sticky='nsew', padx=12)
        tree = self.ttk.Treeview(frame, columns=('name', 'result'), show='headings', selectmode='browse')
        for col, label in (('name', '문서'), ('result', '결과')):
            tree.heading(col, text=label); tree.column(col, width=220, minwidth=80)
        scroll = self.ttk.Scrollbar(frame, command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True); scroll.pack(side='right', fill='y')
        for i, result in enumerate(results):
            tree.insert('', 'end', iid=str(i), values=(self.notes.get(result['source'], {}).get('name', result['source']), STATUS.get(result['status'], result['status'])))
        message_frame, message = self.text_area(view, height=3); message_frame.grid(row=2, column=0, sticky='ew', padx=12, pady=6)
        buttons = self.ttk.Frame(view); buttons.grid(row=3, column=0, sticky='ew', padx=12)
        def current():
            return results[int(tree.selection()[0])] if tree.selection() else {}
        for i, (label, action) in enumerate((('보존본 열기', lambda: self.open_path(current()['copy'])),
                                             ('파일 위치 열기', lambda: self.open_path(current()['copy'], True)),
                                             ('경로 복사', lambda: self.copy_text(current()['copy'])))):
            button = self.ttk.Button(buttons, text=label, command=action, state='disabled')
            button.grid(row=0, column=i, sticky='ew', padx=(0, 4)); buttons.columnconfigure(i, weight=1)
        technical, raw = self.text_area(view, height=5)
        def toggle_technical():
            if technical.winfo_manager(): technical.grid_remove()
            else: technical.grid(row=5, column=0, sticky='ew', padx=12, pady=6)
        self.ttk.Button(view, text='기술 정보 펼치기/접기', command=toggle_technical).grid(row=4, column=0, sticky='w', padx=12, pady=6)
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
        from tkinter import simpledialog
        key = self.single_target()
        accepted, _ = self.action_targets('rename')
        if not key or key not in accepted or self.controller.busy or not self.paths_saved(): return
        name = simpledialog.askstring('다른 이름으로 저장',
                                     '새 파일 이름 (확장자 제외)\n기존 PDF·Markdown은 그대로 남고 새 이름이 관리 대상이 됩니다.\n기존 사용자 메모는 새 파일로 자동 이전되지 않습니다.',
                                     initialvalue=self.notes[key]['name'], parent=self.window)
        if name:
            self.failed = False; self.controller.launch(lambda: self.controller.rename(key, name), kind='sync')
            self.banner.set('새 이름으로 저장할 준비를 하고 있습니다.')
        self.controls()

    def open_vault(self):
        if self.config: self.open_path(self.config['vault'])

    def details(self):
        view = self.tk.Toplevel(self.window); view.title('문제 자세히 보기'); view.geometry('680x520'); view.minsize(480, 360)
        keys = sorted(self.checked_targets())
        frame, text = self.text_area(view, height=9); frame.pack(fill='both', expand=True, padx=12, pady=10)
        for key in keys:
            text.insert('end', f'{self.notes[key]["name"]}\n{guidance(self.notes[key], self.observation(key))}\n\n')
        if not keys: text.insert('end', '문서를 선택하면 현상과 다음 조치를 볼 수 있습니다. 전체 기록은 기술 정보에서 확인하세요.')
        text.configure(state='disabled')
        technical, raw = self.text_area(view, height=8)
        for key in keys:
            raw.insert('end', f'{key}\n{self.notes[key].get("error") or ""}\n{self.observation(key)}\n최근 보존본: {self.notes[key].get("last_preserved_copy")}\n\n')
        if self.config:
            raw.insert('end', f'삼성노트 백업: {self.config["backup"]}\n저장 폴더/Vault: {self.config["vault"]}\n\n')
        raw.insert('end', '\n'.join(self.log_lines) + f'\n\n로그 파일: {self.controller.root/"desktop.log"}')
        raw.configure(state='disabled')
        def toggle():
            if technical.winfo_manager(): technical.pack_forget()
            else: technical.pack(fill='both', expand=True, padx=12, pady=5)
        self.ttk.Button(view, text='기술 정보 펼치기/접기', command=toggle).pack(anchor='w', padx=12)
        tools = self.ttk.Frame(view); tools.pack(fill='x', padx=12, pady=8)
        self.ttk.Button(tools, text='기술 정보 복사', command=lambda: self.copy_text(raw.get('1.0', 'end-1c'))).grid(row=0, column=0, sticky='ew')
        self.ttk.Button(tools, text='백업 내 경로 복사', command=lambda: self.copy_text('\n'.join(keys)), state='normal' if keys else 'disabled').grid(row=0, column=1, sticky='ew', padx=4)
        self.ttk.Button(tools, text='경로 진단', command=lambda: self.diagnose_paths(keys), state='normal' if self.config and not self.controller.busy else 'disabled').grid(row=1, column=0, sticky='ew', pady=4)
        copies = [{'source': key, 'status': 'synced', 'copy': self.notes[key]['last_preserved_copy']} for key in keys if self.notes[key].get('last_preserved_copy')]
        self.ttk.Button(tools, text='최근 보존본 보기', command=lambda: self.results_dialog(copies), state='normal' if copies else 'disabled').grid(row=1, column=1, sticky='ew', padx=4)
        for col in (0, 1): tools.columnconfigure(col, weight=1)

    def diagnose_paths(self, keys):
        from tkinter import messagebox
        if self.controller.busy or not self.config or self.closing: return
        message = ('환경 버전, 경로 정규화 여부, 존재·접근 결과와 실패 단계를 확인합니다.\n'
                   '선택 문서는 임시 사본을 만들어 디코딩까지 검사하며 원본과 저장 파일은 변경하지 않습니다.\n'
                   '기본 결과에는 경로·문서명·코드포인트를 넣지 않습니다. 원문은 별도로 선택해 복사할 수 있습니다.\n\n실행할까요?')
        if messagebox.askyesno('경로 진단', message, parent=self.window):
            self.controller.diagnose_paths(keys, dict(self.selected_paths), self.window.tk.call('info', 'patchlevel'))
            self.controls()

    def diagnostics_dialog(self, report):
        if self.closing: return
        view = self.tk.Toplevel(self.window); view.title('경로 진단 결과'); view.geometry('640x480')
        frame, text = self.text_area(view); frame.pack(fill='both', expand=True, padx=12, pady=10)
        basic = json.dumps({key: value for key, value in report.items() if key != 'raw'}, ensure_ascii=False, indent=2)
        text.insert('end', basic); text.configure(state='disabled')
        self.ttk.Button(view, text='기본 결과 복사 (경로 제외)', command=lambda: self.copy_text(basic)).pack(pady=4)
        def copy_raw():
            from tkinter import messagebox
            if messagebox.askyesno('원문 경로 포함', '개인 경로와 문서명이 포함됩니다. 원문도 복사할까요?', parent=view):
                self.copy_text(json.dumps(report, ensure_ascii=False, indent=2))
        self.ttk.Button(view, text='원문 경로 포함 복사…', command=copy_raw).pack(pady=(0, 10))

    def update_state(self, state):
        focus, scroll = self.tree.focus(), self.tree.yview()
        self.notes = state['notes']; self.observations = state.get('observations', {})
        counts = {key: 0 for key in GROUPS}; visible = []
        for key, entry in self.notes.items():
            item = self.observation(key); category = group(entry, item)
            if item.get('excluded') and category != 'retired': continue
            counts[category] += 1
            if category == self.group and matches(key, entry, item, self.search.get(), self.state_filter.get()): visible.append(key)
        self.checked.intersection_update(visible)
        self.selecting = True
        try:
            visible_set = set(visible)
            for iid in self.tree.get_children():
                if iid not in visible_set: self.tree.delete(iid)
            for index, key in enumerate(visible):
                entry = self.notes[key]
                values = ('☑' if key in self.checked else '☐', '/'.join(p for p in (entry.get('folder', ''), entry['name']) if p), status_text(entry, self.observation(key)))
                if self.tree.exists(key): self.tree.item(key, values=values); self.tree.move(key, '', index)
                else: self.tree.insert('', 'end', iid=key, values=values)
            self.tree_keys = {key: key for key in visible}
            if focus in visible_set: self.tree.focus(focus)
            elif visible: self.tree.focus(visible[0])
            if scroll: self.tree.yview_moveto(scroll[0])
            self.group_counts = counts; self.group_labels()
            self.result_count.set(f'검색 결과 {len(visible)}개 / 이 그룹 {counts[self.group]}개')
            self.refresh_checks()
        finally: self.selecting = False
        if not self.failed and not self.closing:
            self.banner.set(self.issue_summary() or '선택한 문서의 작업 조건을 확인한 뒤 실행할 수 있습니다.')

    def issue_summary(self):
        entries = [(entry, self.observation(key)) for key, entry in self.notes.items()
                   if entry.get('sync_policy') != 'retired' and not self.observation(key).get('excluded')]
        missing = sum(bool(item.get('missing')) or item.get('source_exists') is False for _, item in entries)
        errors = sum(bool(item.get('conflicts')) or entry['status'] in ('error', 'conflict') for entry, item in entries)
        pending = sum(entry['status'] in ('waiting', 'pending', 'queued', 'converting', 'ready_to_publish') for entry, _ in entries)
        unknown = sum(bool(item.get('unavailable')) for _, item in entries)
        return f'파일 없음 {missing}개 · 수정/실패 확인 {errors}개 · 진행/대기 {pending}개 · 접근 미확인 {unknown}개' if missing or errors or pending or unknown else ''

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
        self.banner.set('작업을 완료하지 못했습니다. 문제 자세히 보기에서 확인하세요.')
        if self.settings_dialog:
            self.settings_dialog.saving = False; self.settings_dialog.set_enabled(True)
            self.settings_dialog.message.set('설정을 저장하지 못했습니다. 기술 정보에서 오류를 확인하고 다시 시도하세요.')
        self.show()

    def exit(self, session_end=False):
        if self.closing: return
        if self.settings_dialog:
            if not self.settings_dialog.close(force=session_end or self.controller.busy): return
        self.closing = True; self.startup = False; self.pending_offer = None; self.pending_results = []
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
                    if self.config is None or pair_key(self.config) != pair_key(value): self.checked.clear()
                    self.config = value; self.update_config_summary()
                    if self.settings_dialog and self.settings_dialog.saving: self.settings_dialog.close(force=True)
                elif kind == 'locations': self.locations = value
                elif kind == 'state': self.update_state(value)
                elif kind == 'resolution_offer':
                    if not self.closing: self.pending_offer = value
                elif kind == 'results':
                    if not self.closing: self.pending_results.extend(value)
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
    window = tk.Tk()
    window.withdraw()
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
        Application(window, controller, native, tray, startup=args.startup)
        if not args.startup or not tray or not tray.visible: window.deiconify()
        window.mainloop()
    except Exception as error:
        messagebox.showerror('SNo2md 실행 오류', str(error), parent=window)
        controller.stop()
        if controller.thread: controller.thread.join()
        if tray: tray.close()
        controller.close()
        window.destroy()
        raise


if __name__ == '__main__':
    main()
