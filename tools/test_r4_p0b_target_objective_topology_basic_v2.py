from pathlib import Path
from collections import Counter,defaultdict
import json,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research/r4_v0/p0_provenance_v1';D=R/'r4_p0b_target_objective_topology_rows_v2.csv';FUT=R/'r4_p0b_target_objective_topology_future_rows_v2.csv';STATE=R/'r4_p0b_target_objective_topology_state_rows_v2.csv';OUT=R/'r4_p0b_target_objective_topology_basic_v2.json';H5=5000;H15=15000

def ps(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);return None if not len(a) or not len(b) else float(np.mean(a[:,None]>b[None,:])+.5*np.mean(a[:,None]==b[None,:]))
d=pd.read_csv(D).sort_values(['market_id','t','parent_id']).reset_index(drop=True);pair=Counter();sim=Counter();lc=Counter();lm=defaultdict(set);fut=[];state=[]
for mid,g in d.groupby('market_id',sort=False):
 g=g.sort_values(['t','parent_id']).reset_index(drop=True);ts=g.t.to_numpy(np.int64);keys=g.objective_key.astype(str).to_numpy();fam=g.objective_family.astype(str).to_numpy();sides=g.side.astype(str).to_numpy();open_last={}
 for i,cur in g.iterrows():
  t=int(cur.t);lo=np.searchsorted(ts,t-H15,'left');hi=np.searchsorted(ts,t,'left')
  if hi>lo:
   k=keys[lo:hi];s=sides[lo:hi];same=(k==str(cur.objective_key));ss=(s==str(cur.side));pair['rows']+=len(k);pair['same']+=int(same.sum());pair['diff']+=int((~same).sum());pair['sameSide']+=int(ss.sum());pair['sameSideSame']+=int((ss&same).sum());pair['sameSideDiff']+=int((ss&~same).sum())
  st=np.where(ts==t)[0]
  if len(st)>1:
   eq=(keys[st]==str(cur.objective_key));sim['rows']+=len(st)-1;sim['same']+=int(eq.sum())-1;sim['diff']+=int((~eq).sum())
  key=str(cur.objective_key);sm=key in open_last and t-open_last[key]<=H15;other=any(k!=key and t-v<=H15 for k,v in open_last.items());name='ACCUMULATE_SAME_OBJECTIVE' if sm else 'OPEN_DIFFERENT_OBJECTIVE_PARALLEL' if other else 'OPEN_NEW_OBJECTIVE';lc[name]+=1;lm[name].add(int(mid));open_last[key]=t
  z=g[(g.t>t)&(g.t<=t+H5)]
  if len(z):
   same=bool((z.objective_key==cur.objective_key).any());diff=bool((z.objective_key!=cur.objective_key).any())
   if same!=diff:q=cur.to_dict();q['future_different_objective_5s']=int(diff);fut.append(q)
  if cur.objective_family=='STATE_SHAPING':
   pri=g[(g.t<t)&(g.t>=t-H15)&(g.objective_family=='PAIR_BALANCE')];q=cur.to_dict();q['recent_pair_balance_roots_15s']=int(pri.order_hash.nunique());q['state_shaping_recent_parallel']=int(len(pri)>0);q['state_shaping_live_parallel']=int(cur.weak_active_roots>0);state.append(q)
 end=int(g.t.max())
 for k,v in open_last.items():
  if end-v>H15:lc['OBSERVED_MEMORY_RETIRE']+=1;lm['OBSERVED_MEMORY_RETIRE'].add(int(mid))
f=pd.DataFrame(fut);s=pd.DataFrame(state);f.to_csv(FUT,index=False);s.to_csv(STATE,index=False);sp=s[s.state_shaping_recent_parallel==1];ss=s[s.state_shaping_recent_parallel==0];cols=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','weak_unresolved_shares','weak_progress_ratio','weak_fill_shares_5s'];anat={c:{'parallelMedian':float(sp[c].median()) if len(sp) else None,'soloMedian':float(ss[c].median()) if len(ss) else None,'parallelGreaterProbability':ps(sp[c].dropna(),ss[c].dropna())} for c in cols};out={'coverage':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'familySupport':{str(k):{'rows':int(len(x)),'markets':int(x.market_id.nunique())} for k,x in d.groupby('objective_family')}},'sameVsDifferentObjective':{'rows':int(pair['rows']),'sameObjectivePairs':int(pair['same']),'differentObjectivePairs':int(pair['diff']),'sameSidePairs':int(pair['sameSide']),'sameSideSameObjective':int(pair['sameSideSame']),'sameSideDifferentObjective':int(pair['sameSideDiff']),'sameTimestampDirectedPairs':int(sim['rows']),'sameTimestampSameObjective':int(sim['same']),'sameTimestampDifferentObjective':int(sim['diff'])},'stateShapingParallel':{'stateShapingOpenings':int(len(s)),'recentPairBalanceParallel':int(len(sp)),'recentParallelRate':float(len(sp)/len(s)),'recentParallelMarkets':int(sp.market_id.nunique()),'livePairBalanceParallel':int(s.state_shaping_live_parallel.sum()),'liveParallelRate':float(s.state_shaping_live_parallel.mean()),'liveParallelMarkets':int(s.loc[s.state_shaping_live_parallel==1,'market_id'].nunique()),'strictPastAntecedentAnatomy':anat},'futureRows':int(len(f)),'objectiveGroupLifecycleObservation':{'counts':dict(lc),'markets':{k:len(v) for k,v in lm.items()}}};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))