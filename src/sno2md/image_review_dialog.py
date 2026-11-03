"""Group decisions and a reusable hide library; no per-image approval queue."""
import math

from .gui_text import display
from .image_review import ImageReviewStore, ROLES, matched_rule


class ImageReviewDialog:
    def __init__(self,parent,root,distance=8,*,tab='groups',app=None,documents=None,on_apply=None,on_close=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk,self.ttk=tk,ttk;self.parent=parent;self.app=app if app is not None else parent.app
        self.documents=documents;self.on_apply=on_apply;self.on_close=on_close;self.changed=False
        self.store=ImageReviewStore(root);self.distance=distance;self.closed=False
        self._preview_limit=120
        self.window=tk.Toplevel(parent.window);self.window.title('반복 이미지 검토')
        if hasattr(self.app,'size'):self.app.size(self.window,900,620,600,420)
        else:self.window.geometry('900x620');self.window.minsize(600,420)
        self.window.transient(parent.window);self.window.grab_set()
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(0,weight=1)
        self.notebook=notebook=ttk.Notebook(self.window);notebook.grid(row=0,column=0,sticky='nsew',padx=12,pady=12)
        documents=ttk.Frame(notebook,padding=10);library=ttk.Frame(notebook,padding=10)
        notebook.add(documents,text='문서별 그룹');notebook.add(library,text='자동 무시 사진')
        documents.columnconfigure(0,weight=1);documents.rowconfigure(2,weight=1,minsize=64)
        self.document_box=ttk.Combobox(documents,state='readonly');self.document_box.grid(row=0,column=0,sticky='ew')
        self.document_box.bind('<<ComboboxSelected>>',lambda e:self.show_document())
        self.summary=tk.StringVar(self.window);ttk.Label(documents,textvariable=self.summary,wraplength=740).grid(row=1,column=0,sticky='ew',pady=8)
        self.groups=self.tree(documents,('kind','count','pages','decision'),('종류','표시 횟수','원본 쪽','저장한 결정'),row=2)
        self.groups.bind('<<TreeviewSelect>>',lambda e:self.preview_group())
        preview=ttk.Frame(documents);preview.grid(row=3,column=0,sticky='ew',pady=8);preview.columnconfigure(0,weight=1)
        self.group_preview=ttk.Label(preview,anchor='center');self.group_preview.grid(row=0,column=0,sticky='ew')
        self.variant_text=tk.StringVar(self.window)
        ttk.Label(preview,textvariable=self.variant_text).grid(row=0,column=1,padx=4)
        ttk.Button(preview,text='다른 변형 보기',command=self.next_variant).grid(row=0,column=2)
        actions=ttk.Frame(documents);actions.grid(row=4,column=0,sticky='ew')
        for label,choice in [('이 문서 숨김','hide'),('앞으로도 숨김','global'),('이 문서 보존','keep'),('문서 결정 해제','clear')]:
            ttk.Button(actions,text=label,command=lambda c=choice:self.decide(c)).pack(side='left',padx=(0,5))
        ttk.Label(documents,text='그룹을 여러 개 선택해 한 번에 결정할 수 있습니다. 검토하지 않은 그림·수식은 보존합니다.',wraplength=740).grid(row=5,column=0,sticky='ew',pady=(8,0))
        library.columnconfigure(0,weight=1);library.rowconfigure(1,weight=1,minsize=64)
        ttk.Label(library,text='직접 승인한 그룹만 다른 문서에 자동 적용합니다. 해제하면 다음 동기화에서 다시 표시됩니다.',wraplength=740).grid(row=0,column=0,sticky='ew',pady=(0,8))
        self.library=self.tree(library,('kind','title','variants'),('종류','승인한 문서','검토한 변형 수'),row=1)
        self.library.bind('<<TreeviewSelect>>',lambda e:self.preview_rule())
        self.rule_preview=ttk.Label(library,anchor='center');self.rule_preview.grid(row=2,column=0,sticky='ew',pady=8)
        ttk.Button(library,text='선택한 자동 숨김 해제',command=self.remove_rules).grid(row=3,column=0,sticky='w')
        footer=ttk.Frame(self.window,padding=(12,0,12,12));footer.grid(row=1,column=0,sticky='ew');footer.columnconfigure(0,weight=1)
        self.message=tk.StringVar(self.window,value='결정은 즉시 저장되며 다음 동기화에서 적용됩니다. 보관한 원본 이미지는 유지합니다.')
        ttk.Label(footer,textvariable=self.message,wraplength=650).grid(row=0,column=0,sticky='ew')
        if on_apply:
            self.message.set('사진을 확인하고 그룹의 숨김 또는 보존을 선택한 뒤 결과에 적용하세요.')
            ttk.Button(footer,text='결과에 적용',command=self.apply).grid(row=0,column=1,padx=(8,0))
        ttk.Button(footer,text='닫기',command=self.close).grid(row=0,column=2,padx=(8,0))
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Configure>',self.resized,add=True)
        def wrap_labels(widget):
            if isinstance(widget,ttk.Label) and widget.cget('wraplength'):
                widget.bind('<Configure>',lambda e:e.widget.configure(wraplength=max(100,e.width)),add=True)
            for child in widget.winfo_children():wrap_labels(child)
        wrap_labels(self.window)
        self.reload()
        self.select_tab(tab)

    def select_tab(self,tab):
        self.notebook.select(1 if tab=='library' else 0)
        self.window.title('자동 무시 사진 관리' if tab=='library' else '반복 이미지 검토')

    def tree(self,parent,columns,headings,row):
        ttk=self.ttk
        frame=ttk.Frame(parent);frame.grid(row=row,column=0,sticky='nsew');frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        tree=ttk.Treeview(frame,columns=columns,show='headings',selectmode='extended',height=7)
        for column,heading in zip(columns,headings):tree.heading(column,text=heading);tree.column(column,width=130,minwidth=65,stretch=True)
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0,column=0,sticky='nsew');scrollbar.grid(row=0,column=1,sticky='ns')
        return tree

    def current_document(self):
        index=self.document_box.current()
        return self.data['documents'][index] if 0<=index<len(self.data['documents']) else None

    def reload(self):
        current=self.current_document()['id'] if hasattr(self,'data') and self.current_document() else None
        try:self.data=self.store.snapshot()
        except Exception as error:
            self.message.set(f'검토 목록을 읽지 못했습니다: {error}');self.data=dict(documents=[],rules=[])
        if self.documents is not None:
            self.data['documents']=[d for d in self.data['documents'] if d['id'] in self.documents]
        self.document_box.configure(values=[display(d['title'] or '이름 없는 문서') for d in self.data['documents']])
        if self.data['documents']:
            self.document_box.current(next((i for i,d in enumerate(self.data['documents']) if d['id']==current),0))
        self.show_document();self.library.delete(*self.library.get_children())
        docs={d['id']:d['title'] for d in self.data['documents']}
        for r in self.data['rules']:
            if r['scope']=='global':self.library.insert('', 'end',iid=r['id'],values=(ROLES.get(r['role'],r['role']),display(docs.get(r['document'],'이전 문서')),len(r['prototypes'])))

    def show_document(self):
        self.groups.delete(*self.groups.get_children());self.group_preview.configure(image='',text='');self._group_photo=None
        self._variants=[];self.variant_text.set('')
        doc=self.current_document()
        if not doc:
            self.summary.set('문서를 동기화하면 반복 장식 후보를 그룹으로 표시합니다.');return
        report=doc['report'];rules=[r for r in self.data['rules'] if r['scope']=='global' or r['document']==doc['id']]
        self.summary.set(f'장식 후보 {len(report["groups"])}그룹 · 마지막 분석: 전체 {report["objects"]}개 중 {report["objects"]-report["hidden_count"]}개 보존, {report["hidden_count"]}개 숨김')
        for g in report['groups']:
            rule=matched_rule(g['prototypes'][0],g['role'],rules,self.distance)
            decision='검토 전 · 보존' if not rule else ('이 문서 보존' if rule['action']=='keep' else '앞으로도 숨김' if rule['scope']=='global' else '이 문서 숨김')
            pages=', '.join(map(str,g['pages'][:8]))+('…' if len(g['pages'])>8 else '')
            self.groups.insert('','end',iid=g['id'],values=(g['label'],g['count'],pages,decision))
        if not report['groups']:self.group_preview.configure(text='반복 장식 후보가 없습니다. 본문 이미지에 대한 개별 승인은 필요하지 않습니다.')
        else:
            first=report['groups'][0]['id'];self.groups.selection_set(first);self.preview_group()

    def preview(self,label,sha,attribute):
        try:
            photo=self.tk.PhotoImage(master=self.window,file=str(self.store.image_path(sha)))
            factor=max(1,math.ceil(photo.width()/300),math.ceil(photo.height()/self._preview_limit))
            if factor>1:photo=photo.subsample(factor,factor)
            setattr(self,attribute,photo);label.configure(image=photo,text='')
        except Exception as error:label.configure(image='',text=f'대표 이미지를 열 수 없습니다: {error}')

    def preview_group(self):
        doc=self.current_document();selected=self.groups.selection()
        if doc and selected:
            g=next(g for g in doc['report']['groups'] if g['id']==selected[0])
            self._variants=g['prototypes'];self._variant_index=0;self.show_variant()

    def show_variant(self):
        if getattr(self,'_variants',None):
            self.preview(self.group_preview,self._variants[self._variant_index]['sha'],'_group_photo')
            self.variant_text.set(f'{self._variant_index+1}/{len(self._variants)}')

    def next_variant(self):
        if getattr(self,'_variants',None):
            self._variant_index=(self._variant_index+1)%len(self._variants);self.show_variant()

    def resized(self,event):
        if event.widget is not self.window:return
        limit=60 if event.height<520 else 120
        if limit!=self._preview_limit:
            self._preview_limit=limit;self.show_variant();self.preview_rule()

    def preview_rule(self):
        selected=self.library.selection()
        if selected:
            r=next(r for r in self.data['rules'] if r['id']==selected[0]);self.preview(self.rule_preview,r['prototypes'][0]['sha'],'_rule_photo')

    def decide(self,choice):
        if self.app.controller.busy or self.app.closing:return
        doc=self.current_document();selected=self.groups.selection()
        if not doc or not selected:self.message.set('검토할 그룹을 먼저 선택하세요.');return
        try:
            for identity in selected:
                self.store.decision(doc['id'],identity,choice);self.changed=True
            self.reload();self.message.set(f'{len(selected)}개 그룹의 결정을 저장했습니다. '+('결과에 적용을 누르면 다시 변환합니다.' if self.on_apply else '다음 동기화에서 적용됩니다.'))
        except Exception as error:
            self.reload();self.message.set(f'결정 중 오류: {error}. 이미 저장된 결정은 목록에 표시됩니다.')

    def remove_rules(self):
        if self.app.controller.busy or self.app.closing:return
        selected=self.library.selection()
        try:
            for identity in selected:
                self.store.remove_rule(identity);self.changed=True
            self.reload();self.rule_preview.configure(image='',text='');self._rule_photo=None
            self.message.set('자동 숨김을 해제했습니다. 다음 동기화에서 이미지를 다시 표시합니다.')
        except Exception as error:self.message.set(str(error))

    def apply(self):
        if self.app.controller.busy or self.app.closing:return
        if not self.changed:self.message.set('먼저 그룹의 숨김 또는 보존을 선택하세요.');return
        callback=self.on_apply;self.close(notify=False)
        callback()

    def close(self,notify=True):
        if self.closed:return
        self.closed=True;self.window.grab_release();self.window.destroy();self.parent.review_dialog=None
        if self.parent is not self.app and self.parent.window.winfo_exists() and not self.app.closing:self.parent.window.grab_set()
        if notify and self.on_close:self.on_close(self.changed)
