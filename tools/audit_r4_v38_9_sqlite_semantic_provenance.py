from __future__ import annotations
import argparse, json, sqlite3, time
from pathlib import Path

EXTS={'.db','.sqlite','.sqlite3'}

def qident(s:str)->str:
    return '"'+s.replace('"','""')+'"'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',required=True)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    ids=[int(x) for x in json.loads(Path(args.manifest).read_text(encoding='utf-8'))['ids']]
    ph=','.join('?' for _ in ids)
    dbs=sorted([p for p in Path('data/research').rglob('*') if p.is_file() and p.suffix.lower() in EXTS],key=lambda p:str(p))
    recs=[]; errs=[]; t0=time.time()
    for p in dbs:
        d={'path':str(p).replace('\\','/'),'size_bytes':p.stat().st_size,'tables':[]}
        try:
            con=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True,timeout=1)
            tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            for t in tables:
                cols=[r[1] for r in con.execute(f'pragma table_info({qident(t)})')]
                mids=[c for c in cols if c.lower() in ('market_id','marketid','market')]
                for c in mids:
                    try:
                        rows=[r[0] for r in con.execute(f'select distinct {qident(c)} from {qident(t)} where {qident(c)} in ({ph})',ids)]
                        rows=[int(x) for x in rows if str(x).isdigit()]
                        if rows:
                            total_distinct=con.execute(f'select count(distinct {qident(c)}) from {qident(t)}').fetchone()[0]
                            total_rows=con.execute(f'select count(*) from {qident(t)}').fetchone()[0]
                            d['tables'].append({'table':t,'column':c,'candidate_ids':sorted(rows),'candidate_count':len(rows),'total_distinct_market_ids':int(total_distinct or 0),'total_rows':int(total_rows or 0)})
                    except Exception as e:
                        errs.append({'path':d['path'],'table':t,'column':c,'error':repr(e)})
            con.close()
        except Exception as e:
            errs.append({'path':d['path'],'error':repr(e)})
        if d['tables']: recs.append(d)
    out={'version':'R4_V38_9_SQLITE_SEMANTIC_PROVENANCE_V1','candidate_ids':ids,'dbs_with_semantic_hits':len(recs),'records':recs,'errors':errs,'elapsed_s':round(time.time()-t0,4),'scientific_note':'Schema-aware provenance anatomy only. Reads market-id membership/cardinality, never winner/outcome values.'}
    Path(args.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'dbs_with_hits':len(recs),'errors':len(errs),'elapsed_s':out['elapsed_s'],'artifact':args.out}))

if __name__=='__main__': main()
