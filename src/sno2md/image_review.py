"""Grouped, reversible review of recurring decoration. Body objects stay visible.

Only explicit user decisions hide images. A global rule keeps its reviewed
prototypes; automatic matches never extend that set or drift through a chain.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

ALGORITHM = 'pymupdf32-dct64-rgb-v1'
ROLES = {'header': '머리말 장식', 'footer': '꼬리말 장식', 'marker': '작은 반복 기호'}
SHA = re.compile(r'[0-9a-f]{64}\Z')


def validate_distance(value):
    if type(value) is not int or not 0 <= value <= 32:
        raise ValueError('repeat image distance must be an integer from 0 to 32')
    return value


def image_role(bbox, width, height, kind):
    if kind != 'picture' or width <= 0 or height <= 0:
        return None
    x0,y0,x1,y1 = [bbox[0]/width,bbox[1]/height,bbox[2]/width,bbox[3]/height]
    w,h = x1-x0,y1-y0
    if w <= 0 or h <= 0:return None
    if y1 <= .15 and h <= .12 and w*h <= .07:return 'header'
    if y0 >= .88 and h <= .12 and w*h <= .12:return 'footer'
    if max(w,h) <= .06 and w*h <= .003 and .6 <= w/h <= 1.6:return 'marker'
    return None


def fingerprint(data, pdf):
    """64 low-frequency DCT bits; decode/resize once, without Pillow/SciPy."""
    import numpy as np
    pix = pdf.Pixmap(data)
    width,height = pix.width,pix.height
    if pix.colorspace.n != 3:pix=pdf.Pixmap(pdf.csRGB,pix)
    # ObjectRenderer emits opaque RGB PNGs. Composite other inputs over white.
    if pix.alpha:
        samples=np.frombuffer(pix.samples,dtype=np.uint8).reshape(height,width,pix.n)
        alpha=samples[:,:,-1:].astype(np.float64)/255
        rgb=samples[:,:,:3].astype(np.float64)+(1-alpha)*255
        pix=pdf.Pixmap(pdf.csRGB,width,height,np.clip(rgb,0,255).astype(np.uint8).tobytes(),False)
    small=pdf.Pixmap(pix,32,32)
    rgb=np.frombuffer(small.samples,dtype=np.uint8).reshape(32,32,small.n)[:,:,:3].astype(np.float64)
    gray=rgb @ np.array([.299,.587,.114])
    matrix=np.cos(np.pi/64*np.arange(32)[:,None]*(2*np.arange(32)[None,:]+1))
    low=(matrix @ gray @ matrix.T)[:8,:8]
    bits=low>np.median(low)
    value=0
    for bit in bits.flat:value=(value<<1)|int(bit)
    return dict(algorithm=ALGORITHM,phash=f'{value:016x}',aspect=width/height,
                width=width,height=height,color=(rgb.mean(axis=(0,1))/255).tolist())


def similar(a,b,distance):
    if a.get('algorithm') != ALGORITHM or b.get('algorithm') != ALGORITHM:return False
    if abs(a['aspect']/b['aspect']-1) > .06:return False
    if max(abs(x-y) for x,y in zip(a['color'],b['color'])) > .10:return False
    return (int(a['phash'],16)^int(b['phash'],16)).bit_count() <= distance


class ImageReviewStore:
    def __init__(self, root):
        original=Path(root).absolute()
        if original.is_symlink() or getattr(original,'is_junction',lambda:False)():raise ValueError('image review root cannot be redirected')
        self.root=original.resolve();self.path=self.root/'review.sqlite3'

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.is_symlink() or getattr(self.root,'is_junction',lambda:False)():raise ValueError('image review root must be a real directory')
        if self.path.is_symlink():raise ValueError('image review database cannot be a symlink')
        connection=sqlite3.connect(self.path,timeout=10)
        connection.row_factory=sqlite3.Row
        try:
            schema=connection.execute('PRAGMA user_version').fetchone()[0]
            if schema not in (0,1):raise ValueError('unsupported image review library version')
            if schema == 0:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS images(sha TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT, source TEXT,
                        markdown TEXT, pdf TEXT, report TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, document TEXT NOT NULL,
                        group_id TEXT NOT NULL, scope TEXT NOT NULL, action TEXT NOT NULL,
                        role TEXT NOT NULL, prototypes TEXT NOT NULL, changed REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS revisions(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                    PRAGMA user_version=1;
                ''')
            with connection:yield connection
        finally:connection.close()

    def image_path(self,sha):
        if not SHA.fullmatch(sha):raise ValueError('invalid review image hash')
        directory=self.root/'images'
        if directory.is_symlink() or getattr(directory,'is_junction',lambda:False)():raise ValueError('review images cannot be redirected')
        path=directory/(sha+'.png')
        if path.is_symlink():raise ValueError('review image cannot be a symlink')
        return path

    def archive(self,sha,data):
        if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('review image bytes changed')
        path=self.image_path(sha);path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            fd,name=tempfile.mkstemp(prefix='.image-',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
                try:os.link(name,path)
                except FileExistsError:pass
            finally:Path(name).unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('review archive image changed')

    def remember(self,sha,data,pdf):
        if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('review image bytes changed')
        with self.db() as db:
            row=db.execute('SELECT metadata FROM images WHERE sha=?',(sha,)).fetchone()
        meta=json.loads(row[0]) if row else None
        if not meta or meta.get('algorithm')!=ALGORITHM:
            meta=fingerprint(data,pdf)
            with self.db() as db:db.execute('INSERT OR REPLACE INTO images VALUES(?,?)',(sha,json.dumps(meta)))
        return dict(meta,sha=sha)

    def revision(self,document):
        if not self.path.exists():return '0:0'
        with self.db() as db:
            values={r['scope']:r['revision'] for r in db.execute('SELECT * FROM revisions WHERE scope IN (?,?)',('*',document))}
        return f'{values.get("*",0)}:{values.get(document,0)}'

    def rules(self,document):
        if not self.path.exists():return []
        with self.db() as db:rows=db.execute("SELECT * FROM rules WHERE scope='global' OR document=? ORDER BY changed DESC",(document,)).fetchall()
        return [dict(r,prototypes=json.loads(r['prototypes'])) for r in rows]

    def decision(self,document,group,choice):
        if choice not in {'hide','global','keep','clear'}:raise ValueError('unknown image review decision')
        with self.db() as db:
            row=db.execute('SELECT report FROM documents WHERE id=?',(document,)).fetchone()
            if not row:raise ValueError('document review not found; synchronize first')
            report=json.loads(row[0]);item=next((g for g in report['groups'] if g['id']==group),None)
            if not item:raise ValueError('image group changed; reload the review')
            scope='global' if choice=='global' else 'document'
            identity=hashlib.sha256((document+'\0'+group+'\0'+scope).encode()).hexdigest()
            # Group representatives can change after source edits or a stricter
            # threshold. Replace the applicable local decision, not just its old ID.
            local=db.execute("SELECT * FROM rules WHERE document=? AND scope='document'",(document,)).fetchall()
            for previous in local:
                if previous['role']==item['role'] and (previous['group_id']==group or any(
                    p['sha']==q['sha'] or similar(p,q,report.get('threshold',2))
                    for p in item['prototypes'] for q in json.loads(previous['prototypes']))):
                    db.execute('DELETE FROM rules WHERE id=?',(previous['id'],))
            if choice=='clear':
                pass
            else:
                # The archived bytes, not a GUI-supplied thumbnail, are authoritative.
                for p in item['prototypes']:
                    if hashlib.sha256(self.image_path(p['sha']).read_bytes()).hexdigest()!=p['sha']:
                        raise ValueError('review image missing or modified')
                db.execute('INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?,?,?,?)',
                    (identity,document,group,scope,'keep' if choice=='keep' else 'hide',item['role'],json.dumps(item['prototypes']),time.time()))
            key='*' if scope=='global' else document
            db.execute('INSERT INTO revisions VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1',(key,))

    def remove_rule(self,identity):
        with self.db() as db:
            row=db.execute('SELECT * FROM rules WHERE id=?',(identity,)).fetchone()
            if row:
                db.execute('DELETE FROM rules WHERE id=?',(identity,))
                key='*' if row['scope']=='global' else row['document']
                db.execute('INSERT INTO revisions VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET revision=revision+1',(key,))

    def snapshot(self):
        if not self.path.exists():return dict(documents=[],rules=[])
        with self.db() as db:
            docs=[dict(r) for r in db.execute('SELECT * FROM documents ORDER BY title,id')]
            rules=[dict(r) for r in db.execute('SELECT * FROM rules ORDER BY changed DESC')]
        for d in docs:d['report']=json.loads(d['report'])
        for r in rules:r['prototypes']=json.loads(r['prototypes'])
        return dict(documents=docs,rules=rules)

    def reset_documents(self):
        """Disconnect document history; keep the explicitly reusable hide library."""
        if not self.path.exists():return
        with self.db() as db:
            db.execute('DELETE FROM documents')
            db.execute("DELETE FROM rules WHERE scope='document'")
            db.execute("DELETE FROM revisions WHERE scope!='*'")

    def commit(self,report,markdown,pdf):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?)',
            (report['document'],report['title'],report['source'],str(Path(markdown).resolve()),str(Path(pdf).resolve()),json.dumps(report,ensure_ascii=False)))


def matched_rule(meta,role,rules,distance):
    matches=[]
    for rule in rules:
        if rule['role']!=role:continue
        if any(meta['sha']==p['sha'] or similar(meta,p,distance) for p in rule['prototypes']):
            matches.append(rule)
    local=[r for r in matches if r['scope']=='document']
    choices=local or matches
    return next((r for r in choices if r['action']=='keep'),choices[0] if choices else None)


class ImageReview:
    def __init__(self,root,document,*,distance=8,defer=False,source=None):
        self.store=ImageReviewStore(root);self.document=document
        self.distance=validate_distance(distance);self.defer=defer;self.source=source

    def prepare(self,note,backgrounds,engine):
        started=time.perf_counter();keys=[]
        for refs in backgrounds:
            for _,_,info in refs:
                key=(info['asset'],info['pdf_page_number']-1)
                if key not in keys:keys.append(key)
        for key in keys:engine.extract(*key)
        originals=dict(engine.pages);revision=self.store.revision(self.document);rules=self.store.rules(self.document)
        records=[];cache={};binary_cache={};decisions={};preserved=0;all_objects=0
        for asset,index in keys:
            text=engine.pages.get((asset,index))
            if text is None:continue
            doc=engine.documents.get(asset)
            if doc is None:continue
            rect=doc[index].rect
            for r in engine.object_reports.get((asset,index),[]):
                if r['action']!='image':continue
                all_objects+=1
                role=image_role(r['bbox'],rect.width,rect.height,r['kind'])
                if role is None:preserved+=1;continue
                start,end=r['output_span'];value=text[start:end]
                m=re.search(r'data:image/png;base64,([A-Za-z0-9+/=]+)',value)
                if not m:continue
                sha=r['image_sha256']
                if sha not in cache:
                    binary_cache[sha]=base64.b64decode(m[1],validate=True)
                    cache[sha]=self.store.remember(sha,binary_cache[sha],engine.pdf)
                meta=cache[sha]
                if (role,sha) not in decisions:decisions[role,sha]=matched_rule(meta,role,rules,self.distance)
                normal=[r['bbox'][0]/rect.width,r['bbox'][1]/rect.height,r['bbox'][2]/rect.width,r['bbox'][3]/rect.height]
                records.append(dict(asset=asset,page=index+1,span=[start,end],bbox=list(r['bbox']),normalized_bbox=normal,
                    role=role,meta=meta,rule=decisions[role,sha]))
        groups=[]
        # Sort by content address so grouping is deterministic. Compare against
        # the fixed representative, never against newly admitted aliases.
        for r in sorted(records,key=lambda r:(r['role'],r['meta']['sha'],r['asset'],r['page'],r['span'])):
            g=next((g for g in groups if g['role']==r['role'] and
                    (g['representative']['sha']==r['meta']['sha'] or similar(g['representative'],r['meta'],self.distance))),None)
            if g is None:
                g=dict(role=r['role'],representative=r['meta'],records=[]);groups.append(g)
            g['records'].append(r)
        candidates=[];hidden=[]
        for g in groups:
            pages={(r['asset'],r['page']) for r in g['records']}
            stable=all(max(r['normalized_bbox'][i] for r in g['records'])-min(r['normalized_bbox'][i] for r in g['records'])<=.06 for i in range(4))
            candidate=(len(pages)>=3 and stable) if g['role']!='marker' else (len(pages)>=5 and len(pages)>=len(keys)*.5)
            known=any(r['rule'] for r in g['records'])
            if not candidate and not known:continue
            prototypes=list({r['meta']['sha']:r['meta'] for r in g['records']}.values())
            for prototype in prototypes:self.store.archive(prototype['sha'],binary_cache[prototype['sha']])
            identity=hashlib.sha256((g['role']+'\0'+g['representative']['sha']).encode()).hexdigest()
            approved=[r for r in g['records'] if r['rule'] and r['rule']['action']=='hide']
            hidden.extend(approved)
            candidates.append(dict(id=identity,role=g['role'],label=ROLES[g['role']],count=len(g['records']),
                pages=sorted({r['page'] for r in g['records']}),prototypes=prototypes,
                representative=g['representative']['sha'],hidden=len(approved),
                occurrences=[dict(asset=r['asset'],page=r['page'],bbox=r['bbox'],sha=r['meta']['sha'],
                                  rule=r['rule']['id'] if r['rule'] else None) for r in g['records']]))
        replacements={}
        for r in hidden:replacements.setdefault((r['asset'],r['page']-1),[]).append(r)
        changed={}
        for key,rows in replacements.items():
            text=originals[key];chunks=[];cursor=0
            for r in sorted(rows,key=lambda r:r['span']):
                start,end=r['span']
                if start<cursor or not 0<=start<end<=len(text):raise ValueError('overlapping review image spans')
                chunks.extend((text[cursor:start],'\n\n'));cursor=end
            chunks.append(text[cursor:]);changed[key]=''.join(chunks)
        report=dict(schema=1,document=self.document,title=note.title,source=str(self.source or note.source),
                    groups=candidates,hidden_count=len(hidden),body_preserved=preserved,objects=all_objects,
                    threshold=self.distance,algorithm=ALGORITHM,policy_revision=revision,
                    seconds=time.perf_counter()-started)
        engine.pages.update(changed)
        return report
