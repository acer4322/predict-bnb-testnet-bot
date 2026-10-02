from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import summary
from tools import compare_r3_vs_r4_management_exact_v7 as v7

OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_for_value_single_fork_v1.json'
class AlwaysRejectPreserve:
    def predict_proba(self,X):
        return np.tile(np.array([[1.0,0.0]]),(len(X),1))

def run_one(mid:int, at_ms:int):
    base=r3ctl.run_market(mid,True); A=summary(base)
    off=v7.run_overlay(mid,False,[]); O=summary(off)
    if A!=O:
        return {'marketId':mid,'atMs':at_ms,'seedEquivalent':False,'R3':A,'overlayOff':O}
    orig_mgmt=v7.mgmt_features; orig_pm=v7.PM
    def gated(c,a,now,f):
        mf,weak=orig_mgmt(c,a,now,f)
        if int(now)!=int(at_ms):
            mf=dict(mf); mf['seconds_left']=999.0
        return mf,weak
    v7.mgmt_features=gated; v7.PM=AlwaysRejectPreserve()
    ev=[]
    try:
        on=v7.run_overlay(mid,True,ev)
    finally:
        v7.mgmt_features=orig_mgmt; v7.PM=orig_pm
    B=summary(on)
    matching=[e for e in ev if int(e.get('atMs',-1))==int(at_ms)]
    return {'marketId':mid,'atMs':at_ms,'seedEquivalent':True,'R3':A,'SINGLE_CROSS':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'event':matching[0] if matching else None,'treatmentExecuted':bool(matching and matching[0].get('forcedCross')),'mgmtStats':on.get('mgmtStats',{})}

def main():
    args=sys.argv[1:]
    if len(args)%2: raise SystemExit('usage: marketId atMs [marketId atMs ...]')
    rows=[]
    for i in range(0,len(args),2):
        r=run_one(int(args[i]),int(args[i+1])); rows.append(r); print(json.dumps(r),flush=True)
    OUT.write_text(json.dumps({'version':'R4_CROSS_FOR_VALUE_SINGLE_FORK_V1','definition':'Paired exact-HFT one-checkpoint management action fork. Same R3 seed/tape/queue; management disabled everywhere except selected baseline-reachable timestamp. At treatment timestamp Formation Path veto is deliberately disabled so one extra CROSS is actually applied. Delta floor is direct simulated action advantage.','rows':rows,'researchOnly':True,'actionAuthority':False},indent=2),encoding='utf-8')
if __name__=='__main__': main()
