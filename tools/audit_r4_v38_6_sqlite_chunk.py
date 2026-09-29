from __future__ import annotations
import argparse, json, sqlite3, time
from pathlib import Path

IDS=['1818108','1818119','1818231','1818273','1818288','1818306','1818483','1818498','1818507','1818529','1818530']
EXTS={'.db','.sqlite','.sqlite3'}

def qident(s:str)->str:
    return '"'+s.replace('"','""')+'"'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--chunk-index',type=int,required=True)
    ap.add_argument('--chunk-count',type=int,default=4)
    ap.add_argument('--out',required=True)
    a=ap.parse_args()
    all_dbs=[]
    for p in Path('data/research').rglob('*'):
        s=str(p).replace('\\','/')
        if p.is_file() and p.suffix.lower() in EXTS and '/r4_v0/' not in '/'+s:
            all_dbs.append(p)
    all_dbs=sorted(all_dbs,key=lambda p:str(p))
    dbs=[p for i,p in enumerate(all_dbs) if i%a.chunk_count==a.chunk_index]
    hits=[]; errors=[]; scanned=[]
    t0=time.time()
    for p in dbs:
        rec={'path':str(p).replace('\\','/'),'tables':0,'columns_checked':0,'elapsed_s':0.0}
        d0=time.time()
        try:
            con=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True,timeout=2)
            con.execute('PRAGMA query_only=ON')
            tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            rec['tables']=len(tables)
            for tbl in tables:
                try: cols=list(con.execute(f'PRAGMA table_info({qident(tbl)})'))
                except Exception as e:
                    errors.append({'path':rec['path'],'table':tbl,'error':'pragma:'+repr(e)}); continue
                for c in cols:
                    col=c[1]; typ=(c[2] or '').upper(); rec['columns_checked']+=1
                    # First exact equality through CAST; catches integer and text market IDs.
                    try:
                        ph=','.join('?' for _ in IDS)
                        sql=f'SELECT CAST({qident(col)} AS TEXT) FROM {qident(tbl)} WHERE CAST({qident(col)} AS TEXT) IN ({ph}) LIMIT 20'
                        rows=list(con.execute(sql,IDS))
                        for (v,) in rows:
                            hits.append({'market_id':str(v),'path':rec['path'],'table':tbl,'column':col,'match':'exact_cast'})
                    except Exception as e:
                        errors.append({'path':rec['path'],'table':tbl,'column':col,'error':'exact:'+repr(e)})
                    # Search likely structured text only; avoids full scans of blobs/numerics.
                    if any(k in typ for k in ('CHAR','TEXT','CLOB')) or typ=='':
                        lname=col.lower()
                        if any(k in lname for k in ('json','meta','payload','data','market','event','context','result','detail','note','info')):
                            for mid in IDS:
                                try:
                                    sql=f'SELECT 1 FROM {qident(tbl)} WHERE instr(CAST({qident(col)} AS TEXT), ?) > 0 LIMIT 1'
                                    if con.execute(sql,(mid,)).fetchone():
                                        hits.append({'market_id':mid,'path':rec['path'],'table':tbl,'column':col,'match':'embedded_text'})
                                except Exception as e:
                                    errors.append({'path':rec['path'],'table':tbl,'column':col,'market_id':mid,'error':'embedded:'+repr(e)})
                                    break
            con.close()
        except Exception as e:
            errors.append({'path':rec['path'],'error':'db:'+repr(e)})
        rec['elapsed_s']=round(time.time()-d0,4); scanned.append(rec)
    unique_hits=[]; seen=set()
    for h in hits:
        k=(h['market_id'],h['path'],h['table'],h['column'],h['match'])
        if k not in seen: seen.add(k); unique_hits.append(h)
    hit_ids=sorted(set(h['market_id'] for h in unique_hits))
    out={
      'version':'R4_V38_6_SQLITE_CHUNK_V1','chunk_index':a.chunk_index,'chunk_count':a.chunk_count,
      'candidate_ids':IDS,'db_total_project_non_r4':len(all_dbs),'dbs_in_chunk':len(dbs),
      'dbs_scanned':len(scanned),'hits':unique_hits,'hit_ids':hit_ids,
      'clean_ids_in_this_chunk':[x for x in IDS if x not in hit_ids],
      'errors':errors,'elapsed_s':round(time.time()-t0,4),
      'scientific_note':'Provenance-only audit. No candidate outcome/V37 scoring read.'
    }
    Path(a.out).parent.mkdir(parents=True,exist_ok=True)
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'chunk':a.chunk_index,'dbs':len(dbs),'hits':hit_ids,'errors':len(errors),'artifact':a.out},ensure_ascii=False))
if __name__=='__main__': main()
