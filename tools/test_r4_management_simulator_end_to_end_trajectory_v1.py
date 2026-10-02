from __future__ import annotations
import json,math,statistics,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OBS=ROOT/'data/research/lan_worker_returns/r4-e2e-late20f-observed-v3/late20f_end_to_end_observed_v1.json'
PRE=P/'r4_management_simulator_end_to_end_trajectory_v1_preregistered.json'
from tools import test_r4_management_simulator_v0_semimarkov_v1 as sm
H=[5,15,30];EPS=1e-9

def roots(rows):
 d=defaultdict(list)
 for r in rows:d[(int(r['marketId']),str(r['checkpointResponsibilityId']))].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d

def global_by_market(rows):
 d=defaultdict(list)
 for r in rows:d[int(r['marketId'])].append(r)
 for k in d:d[k].sort(key=lambda x:int(x['t']))
 return d

def state_at(rows,target):
 if not rows:return None
 # nearest receipt-clock checkpoint within 750ms after/before target
 best=min(rows,key=lambda r:abs(int(r['t'])-target))
 if abs(int(best['t'])-target)>750:return None
 return best

def infer_portfolio(r):
 absn=float(r.get('absNet') or r.get('abs_gap') or 0.0);cov=float(r.get('coverage') or 0.0);ratio=float(r.get('absnet_ratio') or 0.0)
 if ratio>1e-8:gross=absn/ratio
 elif cov<1-1e-8 and absn>0:gross=absn/(1-cov)
 else:return None
 mn=max(0.0,(gross-absn)/2.0);mx=max(mn,(gross+absn)/2.0);weak=str(r.get('weakSide') or r.get('checkpointSide') or 'UP')
 if weak=='UP':up,dn=mn,mx
 else:up,dn=mx,mn
 floor=float(r.get('floor') or 0.0);cost=mn-floor
 return [up,dn,cost]

def ledger_apply(st,side,px,qty):
 up,dn,cost=st
 if side=='UP':up+=qty
 else:dn+=qty
 cost+=qty*px
 gross=up+dn;mn=min(up,dn);absn=abs(up-dn);floor=mn-cost;cov=2*mn/gross if gross>EPS else 0.0
 return {'floor':floor,'absNet':absn,'coverage':cov}

def dev_library():
 dev,_=sm.load_rows();return sm.episode_library(sm.roots(dev))
def donor_probs(lib,r0):
 A=np.vstack([x['v'] for x in lib]);q10=np.quantile(A,.1,axis=0);q90=np.quantile(A,.9,axis=0);sc=np.maximum(1e-6,q90-q10);AN=A/sc;v=sm.vec(r0);dist=np.sum((AN-v/sc)**2,axis=1);kk=min(sm.K,len(lib));inds=np.argpartition(dist,kk-1)[:kk]
 out={}
 for h in H:
  ss=[lib[int(i)]['states'][h] for i in inds];out[h]={'completion':sum(x['completed'] for x in ss)/len(ss),'live':sum(x['live'] for x in ss)/len(ss),'unresolved':sum(x['unresolved'] for x in ss)/len(ss),'progress':sum(x['progress'] for x in ss)/len(ss)}
 return out

def direction(a,b,tol=1e-9):return 1 if b>a+tol else -1 if b<a-tol else 0

def main():
 pre=json.loads(PRE.read_text(encoding='utf-8'));obs=json.loads(OBS.read_text(encoding='utf-8'));rr=roots(obs['lifecycleRows']);gm=global_by_market(obs['lifecycleRows']);lib=dev_library();per={h:[] for h in H};life={h:[] for h in H};usable=0
 for key,seq in rr.items():
  r0=seq[0];init=infer_portfolio(r0)
  if init is None:continue
  probs=donor_probs(lib,r0);usable+=1;mid=key[0];t0=int(r0['t']);unr=float(r0.get('checkpointUnresolvedQty') or 0);side=str(r0.get('checkpointSide') or '');px=float(r0.get('requested_px') or 0)
  for h in H:
   act=state_at(gm[mid],t0+h*1000)
   if act is None:continue
   # factorized composition: semi-Markov completion probability controls expected realized responsibility quantity; positive-fill size is overwhelmingly full after reservation correction.
   q=max(0.0,min(unr,unr*float(probs[h]['completion'])))
   pred=ledger_apply(init,side,px,q);a={'floor':float(act.get('floor') or 0),'absNet':float(act.get('absNet') or act.get('abs_gap') or 0),'coverage':float(act.get('coverage') or 0)};i={'floor':float(r0.get('floor') or 0),'absNet':float(r0.get('absNet') or r0.get('abs_gap') or 0),'coverage':float(r0.get('coverage') or 0)}
   per[h].append({'marketId':mid,'rid':key[1],'actual':a,'predicted':pred,'initial':i})
   # lifecycle actual is inferred from whether this root still has a checkpoint near horizon; completion otherwise from first-row 5s labels propagated only when explicit completion is visible.
   own=state_at(seq,t0+h*1000);actual_live=1.0 if own is not None else 0.0;actual_completed=1.0-actual_live
   life[h].append({'actualCompleted':actual_completed,'predCompleted':probs[h]['completion'],'actualLive':actual_live,'predLive':probs[h]['live']})
 repH={};portfolio_nmae=[];floor_ag=[];abs_ag=[];life_errors=[]
 for h in H:
  xs=per[h];n=len(xs)
  def nmae(k):
   if not xs:return math.inf
   ae=sum(abs(x['predicted'][k]-x['actual'][k]) for x in xs)/n;scale=max(1.0,sum(abs(x['actual'][k]-x['initial'][k]) for x in xs)/n);return ae/scale
  fn=nmae('floor');an=nmae('absNet');cn=nmae('coverage');portfolio_nmae += [fn,an,cn]
  fa=sum(direction(x['initial']['floor'],x['actual']['floor'])==direction(x['initial']['floor'],x['predicted']['floor']) for x in xs)/n if n else 0
  aa=sum(direction(x['initial']['absNet'],x['actual']['absNet'])==direction(x['initial']['absNet'],x['predicted']['absNet']) for x in xs)/n if n else 0;floor_ag.append(fa);abs_ag.append(aa)
  ll=life[h];lc=abs(sum(x['predCompleted'] for x in ll)/len(ll)-sum(x['actualCompleted'] for x in ll)/len(ll)) if ll else math.inf;lv=abs(sum(x['predLive'] for x in ll)/len(ll)-sum(x['actualLive'] for x in ll)/len(ll)) if ll else math.inf;life_errors += [lc,lv]
  repH[str(h)]={'usablePortfolioRoots':n,'floorNMAE':fn,'absNetNMAE':an,'coverageNMAE':cn,'floorDirectionAgreement':fa,'absNetDirectionAgreement':aa,'completionRateAbsError':lc,'liveRateAbsError':lv}
 mean_life=sum(life_errors)/len(life_errors);max_life=max(life_errors);mean_port=sum(portfolio_nmae)/len(portfolio_nmae)
 checks={'minimumUsableRoots':usable>=int(pre['primaryGate']['minimumUsableRoots']),'provenanceInvariantViolations':int(obs.get('duplicateExecutionCredit') or 0)==0 and all(bool(x.get('executionCoreExact')) for x in obs.get('markets',[])),'meanLifecycleRateAbsoluteError':mean_life<=float(pre['primaryGate']['meanLifecycleRateAbsoluteErrorMax']),'maxLifecycleRateAbsoluteError':max_life<=float(pre['primaryGate']['maxLifecycleRateAbsoluteErrorMax']),'meanPortfolioNormalizedAbsoluteError':mean_port<=float(pre['primaryGate']['meanPortfolioNormalizedAbsoluteErrorMax']),'floorDirectionAgreement':sum(floor_ag)/len(floor_ag)>=float(pre['primaryGate']['floorDirectionAgreementMin']),'absNetDirectionAgreement':sum(abs_ag)/len(abs_ag)>=float(pre['primaryGate']['absNetDirectionAgreementMin'])}
 gate=all(checks.values());rep={'version':'R4_MANAGEMENT_SIMULATOR_END_TO_END_TRAJECTORY_V1','researchOnly':True,'validationMarkets':len(obs.get('markets',[])),'validationRoots':len(rr),'usableRoots':usable,'horizons':repH,'summary':{'meanLifecycleRateAbsoluteError':mean_life,'maxLifecycleRateAbsoluteError':max_life,'meanPortfolioNormalizedAbsoluteError':mean_port,'meanFloorDirectionAgreement':sum(floor_ag)/len(floor_ag),'meanAbsNetDirectionAgreement':sum(abs_ag)/len(abs_ag),'checks':checks,'gatePass':gate},'structural':{'executionCoreExactAll':all(bool(x.get('executionCoreExact')) for x in obs.get('markets',[])),'duplicateExecutionCredit':int(obs.get('duplicateExecutionCredit') or 0),'pendingSubmitReservationBlocks':int(obs.get('pendingSubmitReservationBlocks') or 0)},'interpretation':'Frozen composition diagnostic. Portfolio economics are recomputed deterministically from inferred checkpoint shares/cost plus expected weak-side responsibility fills; no learned floor/absNet deltas. If this fails, localize lifecycle/execution/portfolio-world coupling before curriculum scaling.'};(P/'r4_management_simulator_end_to_end_trajectory_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
