from __future__ import annotations
import json, math, sys, warnings
from pathlib import Path
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.r4_cross_for_value_single_fork_v1 import run_one
SRC=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_paired_dataset_v2.json'
PART=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_paired_dataset_v2_partial.json'

def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    targets=[]; meta={}
    for m in src['markets']:
        if not m.get('seedEquivalent'): continue
        for c in m.get('candidates',[]):
            if float(c.get('floor',0)) >= 0: continue
            k=(int(m['marketId']),int(c['atMs']))
            targets.append(k); meta[k]=c
    done={}
    if PART.exists():
        try:
            for r in json.loads(PART.read_text(encoding='utf-8')).get('rows',[]): done[(int(r['marketId']),int(r['atMs']))]=r
        except Exception: pass
    for i,(mid,at) in enumerate(targets,1):
        if (mid,at) in done: continue
        r=run_one(mid,at); r['baselineCandidate']=meta[(mid,at)]
        A=r.get('R3',{}); B=r.get('SINGLE_CROSS',{})
        changed=bool(r.get('treatmentExecuted')) and any(abs(float(B.get(k,0))-float(A.get(k,0)))>1e-9 for k in ['makerFillEvents','makerFilledShares','takerFills','finalAbsNet','worstCaseFloor','makerNet'])
        df=float(r.get('deltaFloor',0) or 0)
        r['economicActionable']=changed
        r['valueClass']='VALUE_POS' if df>1e-9 else 'VALUE_NEG' if df<-1e-9 else 'NO_OP'
        done[(mid,at)]=r
        PART.write_text(json.dumps({'version':'R4_CROSS_FOR_VALUE_PAIRED_DATASET_V2_PARTIAL','rows':list(done.values())},indent=2),encoding='utf-8')
        print(json.dumps({'i':i,'n':len(targets),'marketId':mid,'atMs':at,'class':r['valueClass'],'deltaFloor':df,'changed':changed}),flush=True)
    rows=[done[k] for k in targets if k in done]
    agg={'targets':len(targets),'rows':len(rows),'markets':len(set(r['marketId'] for r in rows)),'seedEquivalent':sum(bool(r.get('seedEquivalent')) for r in rows),'treatmentExecuted':sum(bool(r.get('treatmentExecuted')) for r in rows),'economicActionable':sum(bool(r.get('economicActionable')) for r in rows),'valuePos':sum(r.get('valueClass')=='VALUE_POS' for r in rows),'valueNeg':sum(r.get('valueClass')=='VALUE_NEG' for r in rows),'noOp':sum(r.get('valueClass')=='NO_OP' for r in rows),'meanDeltaFloor':sum(float(r.get('deltaFloor',0) or 0) for r in rows)/max(len(rows),1)}
    OUT.write_text(json.dumps({'version':'R4_CROSS_FOR_VALUE_PAIRED_DATASET_V2','definition':'All negative-floor, seed-equivalent, baseline-reachable management candidates from baseline scanner. Each row is paired exact-HFT KEEP R3 vs one forced CROSS at the same timestamp; all other management disabled.','aggregate':agg,'rows':rows,'researchOnly':True,'actionAuthority':False},indent=2),encoding='utf-8')
    print(json.dumps(agg,indent=2),flush=True)
if __name__=='__main__': main()
