from __future__ import annotations
import argparse,json,mmap,re,time
from pathlib import Path
EXTS={'.db','.sqlite','.sqlite3'}
def load_ids(path):
    o=json.loads(Path(path).read_text(encoding='utf-8')); return [str(x) for x in (o.get('ids') or o.get('candidate_ids') or o.get('market_ids'))]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',required=True); ap.add_argument('--out',required=True); ap.add_argument('--progress',required=True); a=ap.parse_args()
    ids=load_ids(a.manifest); pat=re.compile(b'|'.join(re.escape(x.encode()) for x in sorted(ids,key=len,reverse=True)))
    dbs=sorted([p for p in Path('data/research').rglob('*') if p.is_file() and p.suffix.lower() in EXTS],key=lambda p:str(p))
    hits=[]; errs=[]; scans=[]; t0=time.time(); prog=Path(a.progress); prog.parent.mkdir(parents=True,exist_ok=True)
    for n,p in enumerate(dbs,1):
        d0=time.time(); rec={'path':str(p).replace('\\','/'),'size':p.stat().st_size}
        try:
            if p.stat().st_size:
                with open(p,'rb') as f:
                    with mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as mm:
                        mids=sorted(set(m.group(0).decode() for m in pat.finditer(mm)))
            else: mids=[]
            rec['raw_ascii_hit_ids']=mids
            for mid in mids: hits.append({'market_id':mid,'path':rec['path'],'match':'raw_ascii_regex'})
        except Exception as e: errs.append({'path':rec['path'],'error':repr(e)})
        rec['elapsed_s']=round(time.time()-d0,4); scans.append(rec)
        prog.write_text(json.dumps({'version':'R4_V38_8_RAW_PROGRESS_V1','db_total':len(dbs),'dbs_scanned':n,'hit_ids':sorted(set(h['market_id'] for h in hits)),'errors':len(errs),'elapsed_s':round(time.time()-t0,3)},ensure_ascii=False,indent=2),encoding='utf-8')
    hit_ids=sorted(set(h['market_id'] for h in hits))
    out={'version':'R4_V38_8_PROJECT_SQLITE_RAWREGEX_V1','candidate_ids':ids,'db_total_project':len(dbs),'dbs_scanned':len(scans),'hits':hits,'hit_ids':hit_ids,'clean_ids':[i for i in ids if i not in hit_ids],'errors':errs,'scans':scans,'elapsed_s':round(time.time()-t0,4),'scientific_note':'Raw SQLite provenance-only scan; no V37 outcome/scoring read.'}
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'dbs':len(dbs),'hits':len(hit_ids),'errors':len(errs),'elapsed_s':out['elapsed_s'],'artifact':a.out},ensure_ascii=False))
if __name__=='__main__': main()
