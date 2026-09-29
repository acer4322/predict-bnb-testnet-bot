from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import summary
from tools import compare_r3_vs_r4_management_exact_v7 as v7
OUT=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_v1.json'
class AlwaysPreserve:
    def predict_proba(self,X):
        return np.tile(np.array([[0.0,1.0]]),(len(X),1))

def scan(mid:int):
    A=summary(r3ctl.run_market(mid,True)); orig=v7.PM; v7.PM=AlwaysPreserve(); ev=[]
    try: rep=v7.run_overlay(mid,True,ev)
    finally: v7.PM=orig
    B=summary(rep)
    return {'marketId':mid,'seedEquivalent':A==B,'R3':A,'scan':B,'candidates':[e for e in ev if not e.get('error')],'candidateCount':len([e for e in ev if not e.get('error')])}

def main():
    mids=[int(x) for x in sys.argv[1:]]; rows=[]
    for m in mids:
        r=scan(m);rows.append(r);print(json.dumps({'marketId':m,'seedEquivalent':r['seedEquivalent'],'candidateCount':r['candidateCount'],'first':r['candidates'][0] if r['candidates'] else None}),flush=True)
    OUT.write_text(json.dumps({'version':'R4_BASELINE_CROSS_CANDIDATES_V1','definition':'Exact R3 baseline-reachable management risk candidates. Formation-path expert is monkeypatched to preserve every candidate so scanning itself cannot add management CROSS; summary must remain seed-equivalent to R3.','markets':rows,'researchOnly':True},indent=2),encoding='utf-8')
if __name__=='__main__': main()
