from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features
P=ROOT/'data/research/r4_v0/p0_provenance_v1';MOD=P/'r4_state_shaping_authorization_v11_portable_shadow.joblib';FROZEN=joblib.load(MOD);FEATS=list(FROZEN['features']);MODEL=FROZEN['model'];EPS=1e-9

def mode_memory(history,now):
 h=[x for x in history if x['t']<now];p5=[x for x in h if now-x['t']<=5000];p15=[x for x in h if now-x['t']<=15000];trans=0
 if len(p15)>1:trans=sum(p15[i]['family']!=p15[i-1]['family'] for i in range(1,len(p15)))
 age=0.0
 if h:
  prev=h[-1];start=prev['t'];fm=prev['family'];k=len(h)-1
  while k>0 and h[k-1]['family']==fm and h[k]['t']-h[k-1]['t']<=15000:start=h[k-1]['t'];k-=1
  age=max(0.,(now-start)/1000.)
 return len(p5),len(p15),trans,age

def make_portable(mf,history,now,seconds_left):
 gap=max(float(mf['abs_gap']),18.0);floor=float(mf['floor']);abs_gap=float(mf['abs_gap']);upside=floor+abs_gap;e5,e15,tr,age=mode_memory(history,now)
 return {'seconds_left_norm':seconds_left/300.,'risk_deficit_gap_ratio':float(mf['risk_deficit'])/gap,'floor_gap_ratio':floor/gap,'upside_gap_ratio':upside/gap,'coverage':float(mf['coverage']),'floor_per_gross':float(mf['floor_per_gross']),'events_5s_rate':e5/5.,'events_15s_rate':e15/15.,'transitions_15s_rate':tr/15.,'mode_age_log':float(np.log1p(max(age,0.))),'weak_active_roots':float(mf['weak_active_owners']),'dominant_active_roots':float(mf['dominant_active_owners']),'weak_unresolved_gap_ratio':float(mf['weak_unresolved_shares'])/gap,'dominant_unresolved_gap_ratio':float(mf['dominant_unresolved_shares'])/gap,'weak_progress_ratio':float(mf['weak_progress_ratio']),'dominant_progress_ratio':float(mf['dominant_progress_ratio']),'weak_fill5_gap_ratio':float(mf['weak_fill_shares_5s'])/gap,'dominant_fill5_gap_ratio':float(mf['dominant_fill_shares_5s'])/gap}

def run_market(mid:int,wend:int):
 origbase=base.new_controller;records=[]
 def audit_new(a):
  c=origbase(a);orig_add=c._add_order;history=[]
  def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
   f=r3ctl.current_features(c,now);weak=None;mf=None;family='UNKNOWN';rec=None
   if f is not None:
    mf,weak=mgmt_features(c,a,now,f);strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;family='PAIR_BALANCE' if weak and side==weak else 'STATE_SHAPING' if strong and side==strong else 'UNKNOWN';sec=max(0.,min(300.,(int(wend)-int(now))/1000.))
    if family=='PAIR_BALANCE' and sec>0:
     mf=dict(mf);mf['floor']=float(f.get('floor',0.0));vec=make_portable(mf,history,int(now),sec);arr=np.asarray([[float(vec[k]) for k in FEATS]],float);prob=float(MODEL.predict_proba(arr)[0,1]);phase='FORMATION_180_300' if sec>180 else 'MANAGEMENT_60_180' if sec>=60 else 'PROTECTION_0_60';rec={'marketId':int(mid),'atMs':int(now),'secondsLeft':sec,'phase':phase,'attemptSide':str(side),'reason':str(reason),'pStateShaping':prob,'triggerAt05':bool(prob>=.5),'historyParentsBefore':len(history),'portable':vec}
   made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
   if made and family in {'PAIR_BALANCE','STATE_SHAPING'}:history.append({'t':int(now),'family':family,'side':str(side)})
   if rec is not None:rec['made']=bool(made);records.append(rec)
   return made
  c._add_order=add;return c
 base.new_controller=audit_new
 try:rep=r3ctl.run_market(int(mid),True)
 finally:base.new_controller=origbase
 s=rep['studentRollout'];fp=s['finalPortfolio'];return {'marketId':int(mid),'records':records,'r3Summary':{'makerFillEvents':s['makerFillEvents'],'makerFilledShares':s['makerFilledShares'],'takerFills':s['takerFills'],'finalAbsNet':fp.get('combined_abs_net'),'finalFloor':fp.get('worst_case_floor')}}

def summarize(rows):
 rec=[z for r in rows for z in r.get('records',[])];out={'markets':len(rows),'errors':sum('error' in r for r in rows),'candidatePairBalanceAttempts':len(rec),'madePairBalanceAttempts':sum(bool(x.get('made')) for x in rec),'byPhase':{}}
 for phase in ['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']:
  q=[x for x in rec if x['phase']==phase];p=np.asarray([x['pStateShaping'] for x in q],float)
  out['byPhase'][phase]={'n':len(q),'made':sum(bool(x.get('made')) for x in q),'triggerAt05':sum(bool(x['triggerAt05']) for x in q),'triggerRateAt05':float(np.mean([x['triggerAt05'] for x in q])) if q else None,'meanP':float(p.mean()) if len(p) else None,'p10':float(np.quantile(p,.1)) if len(p) else None,'p50':float(np.quantile(p,.5)) if len(p) else None,'p90':float(np.quantile(p,.9)) if len(p) else None}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--window-map-json',required=True);ap.add_argument('--out',required=True);args=ap.parse_args();ids=json.loads(Path(args.ids_json).read_text());wm=json.loads(Path(args.window_map_json).read_text());rows=[]
 for mid in ids:
  try:r=run_market(int(mid),int(wm[str(int(mid))]))
  except Exception as e:r={'marketId':int(mid),'error':f'{type(e).__name__}:{e}','records':[]}
  rows.append(r);print(mid,'records',len(r.get('records',[])),'error',r.get('error'),flush=True)
 out={'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_1_RUNTIME_TRANSPORT_SAME30','researchOnly':True,'actionAuthority':False,'candidateChanged':False,'mapping':'Maker parent opening history for events/transition/mode-age; execution state for owner/unresolved/progress/fill5; current candidate must be strict-past weak-side PAIR_BALANCE','guards':['shadow only','no order mutation by State-Shaping head','<=180s never authorizes NEW responsibility','no winner/PnL/model-label runtime input'],'summary':summarize(rows),'markets':rows};Path(args.out).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out['summary'],indent=2))
if __name__=='__main__':main()
