from __future__ import annotations
import argparse,json,sqlite3,time
from pathlib import Path
EXTS={'.db','.sqlite','.sqlite3'}
def qi(s): return '"'+s.replace('"','""')+'"'
def load_ids(path):
    obj=json.loads(Path(path).read_text(encoding='utf-8'))
    ids=obj.get('ids') or obj.get('candidate_ids') or obj.get('market_ids')
    if not ids: raise SystemExit('no ids in manifest')
    return [str(x) for x in ids]
def raw_hits(path,ids):
    needles={i:i.encode() for i in ids}; found=set(); overlap=b''
    with open(path,'rb') as f:
        while True:
            b=f.read(4*1024*1024)
            if not b: break
            x=overlap+b
            for i,n in needles.items():
                if i not in found and n in x: found.add(i)
            if len(found)==len(ids): break
            overlap=x[-32:]
    return found

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--manifest',required=True); ap.add_argument('--chunk-index',type=int,required=True); ap.add_argument('--chunk-count',type=int,default=8); ap.add_argument('--out',required=True); a=ap.parse_args()
    ids=load_ids(a.manifest)
    all_dbs=[]
    for p in Path('data/research').rglob('*'):
        if p.is_file() and p.suffix.lower() in EXTS: all_dbs.append(p)
    all_dbs=sorted(all_dbs,key=lambda p:str(p)); dbs=[p for n,p in enumerate(all_dbs) if n%a.chunk_count==a.chunk_index]
    hits=[]; errs=[]; scans=[]; t0=time.time()
    for p in dbs:
        rec={'path':str(p).replace('\\','/'),'size':p.stat().st_size}; d0=time.time()
        try:
            rh=raw_hits(p,ids); rec['raw_ascii_hit_ids']=sorted(rh)
            for mid in rh: hits.append({'market_id':mid,'path':rec['path'],'match':'raw_ascii'})
        except Exception as e: errs.append({'path':rec['path'],'error':'raw:'+repr(e)})
        try:
            con=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True,timeout=2); con.execute('PRAGMA query_only=ON')
            tabs=[r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            checked=0
            for t in tabs:
                try: cols=list(con.execute(f'PRAGMA table_info({qi(t)})'))
                except Exception as e: errs.append({'path':rec['path'],'table':t,'error':'pragma:'+repr(e)}); continue
                for c in cols:
                    col=c[1]; ln=col.lower()
                    if not ('market' in ln or ln in {'id','marketid','market_id','condition_id','conditionid'}): continue
                    checked+=1
                    try:
                        ph=','.join('?'*len(ids)); sql=f'SELECT CAST({qi(col)} AS TEXT) FROM {qi(t)} WHERE CAST({qi(col)} AS TEXT) IN ({ph}) LIMIT 500'
                        for (v,) in con.execute(sql,ids): hits.append({'market_id':str(v),'path':rec['path'],'table':t,'column':col,'match':'schema_exact'})
                    except Exception as e: errs.append({'path':rec['path'],'table':t,'column':col,'error':'query:'+repr(e)})
            rec['schema_columns_checked']=checked; con.close()
        except Exception as e: errs.append({'path':rec['path'],'error':'db:'+repr(e)})
        rec['elapsed_s']=round(time.time()-d0,4); scans.append(rec)
    uniq=[]; seen=set()
    for h in hits:
        k=tuple(sorted(h.items()))
        if k not in seen: seen.add(k); uniq.append(h)
    hit_ids=sorted(set(h['market_id'] for h in uniq))
    out={'version':'R4_V38_8_PROJECT_SQLITE_CHUNK_V1','chunk_index':a.chunk_index,'chunk_count':a.chunk_count,'candidate_ids':ids,'db_total_project':len(all_dbs),'dbs_in_chunk':len(dbs),'dbs_scanned':len(scans),'hits':uniq,'hit_ids':hit_ids,'clean_ids_in_this_chunk':[i for i in ids if i not in hit_ids],'errors':errs,'scans':scans,'elapsed_s':round(time.time()-t0,4),'scientific_note':'Provenance-only. No V37 outcome/scoring read.'}
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'chunk':a.chunk_index,'dbs':len(dbs),'hits':len(hit_ids),'errors':len(errs),'elapsed_s':out['elapsed_s'],'artifact':a.out},ensure_ascii=False))
if __name__=='__main__': main()
