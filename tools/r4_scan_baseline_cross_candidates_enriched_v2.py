from __future__ import annotations
import json,sys,warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings('ignore')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import summary
from tools import compare_r3_vs_r4_management_exact_v8_enriched as v8
OUT=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_enriched_v2.json'
class AlwaysPreserve:
 def predict_proba(self,X): return np.tile(np.array([[0.,1.]]),(len(X),1))
def scan(mid):
 A=summary(r3ctl.run_market(mid,True));orig=v8.PM;v8.PM=AlwaysPreserve();ev=[]
 try: rep=v8.run_overlay(mid,True,ev)
 finally: v8.PM=orig
 B=summary(rep); cs=[e for e in ev if not e.get('error')]
 return {'marketId':mid,'seedEquivalent':A==B,'R3':A,'scan':B,'candidates':cs,'candidateCount':len(cs)}
def main():
 mids=[int(x) for x in sys.argv[1:]]; rows=[]
 for i,m in enumerate(mids,1):
  r=scan(m);rows.append(r);print(json.dumps({'i':i,'n':len(mids),'marketId':m,'seedEquivalent':r['seedEquivalent'],'candidateCount':r['candidateCount']}),flush=True)
 OUT.write_text(json.dumps({'version':'R4_BASELINE_CROSS_CANDIDATES_ENRICHED_V2','definition':'Exact baseline-reachable candidate scan with complete strict-past management/execution features logged. Formation-path preserve forced so scan itself cannot add management CROSS.','markets':rows,'researchOnly':True},indent=2),encoding='utf-8')
if __name__=='__main__':main()
