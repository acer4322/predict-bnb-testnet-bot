from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as ctl
DB=ROOT/'data/hft_forward_paper_v1.db'; OUT=ROOT/'data/research/r3_v0/r31_r3s_paper_lifecycle_integration_v1.json'

def mids(limit=3):
 c=sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro",uri=True); rows=[int(r[0]) for r in c.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by completed_at_ms desc limit ?",(limit,))];c.close();return rows

def summarize(rep):
 sr=rep['studentRollout']; p=sr['finalPortfolio']; ev=rep['r3Control']['events']
 return {'makerFillEvents':sr['makerFillEvents'],'makerFilledShares':sr['makerFilledShares'],'takerFills':sr['takerFills'],'finalAbsNet':p.get('combined_abs_net'),'worstCaseFloor':p.get('worst_case_floor'),'makerNet':p.get('maker_net'),'controlEvents':len(ev),'vetoStrong':rep['r3Control']['vetoStrong'],'weakSubstitutions':rep['r3Control']['weakSubstitutions'],'contextualEvents':rep['r3Control']['contextualEvents'],'contextTypes':sorted(set(str(e.get('contextType')) for e in ev))}

def main():
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--limit',type=int,default=3);a=ap.parse_args(); rows=[]
 for m in mids(a.limit):
  A=ctl.run_market(m,False);B=ctl.run_market(m,True); sa,sb=summarize(A),summarize(B)
  rows.append({'marketId':m,'baseline':sa,'r31R3S':sb,'executionChanged':sa!=sb,'paperOnly':True})
 out={'version':'R31_R3S_PAPER_LIFECYCLE_INTEGRATION_V1','createdAtMs':int(time.time()*1000),'paperOnly':True,'echtgeldAuthority':False,'markets':rows,'summary':{'markets':len(rows),'contextObserved':sum(r['r31R3S']['contextualEvents']>0 for r in rows),'executionChanged':sum(r['executionChanged'] for r in rows),'errors':0}}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out['summary']))
if __name__=='__main__':main()
