from __future__ import annotations
import argparse,json,mmap,time
from pathlib import Path

def has_boundary(mm, needle:bytes):
    start=0; n=len(needle); L=len(mm)
    while True:
        i=mm.find(needle,start)
        if i<0: return False
        prev=mm[i-1] if i>0 else None
        nxt=mm[i+n] if i+n<L else None
        if (prev is None or not 48<=prev<=57) and (nxt is None or not 48<=nxt<=57): return True
        start=i+1

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--raw-artifact',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    src=json.loads(Path(a.raw_artifact).read_text(encoding='utf-8'))
    pairs=sorted(set((str(h['market_id']),h['path']) for h in src.get('hits',[])))
    verified=[]; false=[]; errs=[]; t0=time.time()
    bypath={}
    for mid,p in pairs: bypath.setdefault(p,[]).append(mid)
    for p,mids in bypath.items():
        try:
            pp=Path(p)
            if not pp.exists():
                errs.append({'path':p,'error':'missing'}); continue
            with open(pp,'rb') as f, mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as mm:
                for mid in sorted(set(mids),key=int):
                    rec={'market_id':mid,'path':p}
                    (verified if has_boundary(mm,mid.encode()) else false).append(rec)
        except Exception as e: errs.append({'path':p,'error':repr(e)})
    candidate_ids=[str(x) for x in src.get('candidate_ids',[])]
    verified_ids=sorted(set(x['market_id'] for x in verified),key=int)
    out={'version':'R4_V38_10_BOUNDARY_VERIFY_RAW_HITS_V1','source_raw_artifact':a.raw_artifact,'candidate_ids':candidate_ids,'raw_pair_count':len(pairs),'verified_boundary_hits':verified,'verified_hit_ids':verified_ids,'false_positive_pairs':false,'false_positive_pair_count':len(false),'clean_ids':[x for x in candidate_ids if x not in verified_ids],'errors':errs,'elapsed_s':round(time.time()-t0,4),'scientific_note':'Every true digit-boundary hit must be a subset of prior raw substring hits; verifier rechecks only those pairs, eliminating decimal/timestamp substring false positives. No winner/outcome values read.'}
    Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'candidates':len(candidate_ids),'raw_pairs':len(pairs),'verified_hit_ids':len(verified_ids),'clean_ids':len(out['clean_ids']),'false_pairs':len(false),'errors':len(errs),'elapsed_s':out['elapsed_s'],'artifact':a.out}))
if __name__=='__main__': main()
