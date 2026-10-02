from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import summary
from tools import compare_r3_vs_r4_management_exact_v7 as v7
OUT=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_checkpoint_v2.json'
class AlwaysPreserve:
    def predict_proba(self,X): return np.tile(np.array([[0.0,1.0]]),(len(X),1))
def scan(mid:int):
    A=summary(r3ctl.run_market(mid,True)); orig=v7.PM; v7.PM=AlwaysPreserve(); ev=[]
    try: rep=v7.run_overlay(mid,True,ev)
    finally: v7.PM=orig
    B=summary(rep); cand=[e for e in ev if not e.get('error')]
    return {'marketId':mid,'seedEquivalent':A==B,'R3':A,'scan':B,'candidates':cand,'candidateCount':len(cand)}
def save(rows):
    OUT.write_text(json.dumps({'version':'R4_BASELINE_CROSS_CANDIDATES_CHECKPOINT_V2','definition':'Exact R3 baseline-reachable candidates; each completed market checkpointed atomically. Scanner itself preserves candidates and must remain seed-equivalent.','researchOnly':True,'markets':rows},indent=2),encoding='utf-8')
def main():
    mids=[int(x) for x in sys.argv[1:]]; rows=[]
    for i,m in enumerate(mids,1):
        r=scan(m); rows.append(r); save(rows)
        print(json.dumps({'i':i,'n':len(mids),'marketId':m,'seedEquivalent':r['seedEquivalent'],'candidateCount':r['candidateCount']}),flush=True)
    print(json.dumps({'markets':len(rows),'seedEquivalent':sum(x['seedEquivalent'] for x in rows),'candidates':sum(x['candidateCount'] for x in rows)}),flush=True)
if __name__=='__main__': main()
