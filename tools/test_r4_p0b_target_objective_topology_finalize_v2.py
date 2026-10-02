from __future__ import annotations
import sys,json,sqlite3
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'; ROWS=R/'r4_p0b_target_objective_topology_rows_v2.csv'; OFF=ROOT/'data/target_wallet_official_v1.db'
PRE=R/'r4_p0b_target_objective_topology_preposition_rows_v2.csv'; STATE=R/'r4_p0b_target_objective_topology_state_rows_v2.csv'; FUT=R/'r4_p0b_target_objective_topology_future_rows_v2.csv'
STRUCT=R/'r4_p0b_target_objective_topology_structural_v2.json'; MODELS=R/'r4_p0b_target_objective_topology_models_v2.json'; OUT=R/'r4_p0b_target_objective_topology_v2.json'; STATUS=R/'r4_p0b_target_objective_topology_lane_status_v2.json'
H5=5000;H15=15000;EPS=1e-9
GEOM=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross']; MEM=['seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s']; ROLEMEM=['recent_pair_balance_events_15s','recent_state_shaping_events_15s','distinct_objective_keys_15s']; OWNER=['weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']; CURR=['current_pair_balance','current_state_shaping']
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
def qdf(sql,params=()):
 c=sqlite3.connect(f'file:{OFF.as_posix()}?mode=ro',uri=True);c.execute('pragma query_only=on');x=pd.read_sql_query(sql,c,params=params);c.close();return x
def psup(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);a=a[np.isfinite(a)];b=b[np.isfinite(b)]
 return None if not len(a) or not len(b) else float(np.mean(a[:,None]>b[None,:])+.5*np.mean(a[:,None]==b[None,:]))
def load_events(mids):
 ph=','.join('?'*len(mids));e=qdf(f"select market_id,lower(order_hash) order_hash,role,event_ms,shares from wallet_shadow_target_events where market_id in ({ph}) and quote_type='BID' order by market_id,event_ms,id",mids);e=e[e.role.astype(str).str.upper().eq('MAKER')].copy();e.event_ms=e.event_ms.astype(np.int64)
 idx={}
 for (m,h),g in e.groupby(['market_id','order_hash']):
  g=g.sort_values('event_ms');idx[(int(m),str(h))]=(g.event_ms.to_numpy(np.int64),np.cumsum(g.shares.fillna(0).to_numpy(float)))
 return idx
def confirmed(idx,m,h,t):
 z=idx.get((int(m),str(h)))
 if z is None:return 0.
 ts,cs=z;k=np.searchsorted(ts,int(t),'left');return float(cs[k-1]) if k else 0.
def phase_struct():
 d=pd.read_csv(ROWS);d=d.sort_values(['market_id','t','parent_id']).reset_index(drop=True);mids=tuple(sorted(d.market_id.astype(int).unique()));idx=load_events(mids)
 pair=Counter();sim=Counter();pre=[];state=[];fut=[];lc=Counter();lm=defaultdict(set)
 for mid,g in d.groupby('market_id',sort=False):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True);ts=g.t.to_numpy(np.int64);keys=g.objective_key.astype(str).to_numpy();fam=g.objective_family.astype(str).to_numpy();sides=g.side.astype(str).to_numpy();open_last={}
  for i,cur in g.iterrows():
   t=int(cur.t);lo=np.searchsorted(ts,t-H15,'left');hi=np.searchsorted(ts,t,'left')
   for j in range(lo,hi):
    same=int(keys[j]==str(cur.objective_key));ss=int(sides[j]==str(cur.side));pair['rows']+=1;pair['same']+=same;pair['diff']+=1-same;pair['sameSide']+=ss;pair['sameSideSame']+=ss*same;pair['sameSideDiff']+=ss*(1-same)
   st=np.where(ts==t)[0]
   for j in st:
    if j!=i:sim['rows']+=1;sim['same']+=int(keys[j]==str(cur.objective_key));sim['diff']+=int(keys[j]!=str(cur.objective_key))
   key=str(cur.objective_key);same_mem=key in open_last and t-open_last[key]<=H15;other=any(k!=key and t-v<=H15 for k,v in open_last.items());name='ACCUMULATE_SAME_OBJECTIVE' if same_mem else 'OPEN_DIFFERENT_OBJECTIVE_PARALLEL' if other else 'OPEN_NEW_OBJECTIVE';lc[name]+=1;lm[name].add(int(mid));open_last[key]=t
   z5=g[(g.t>t)&(g.t<=t+H5)]
   if len(z5):
    same=bool((z5.objective_key==cur.objective_key).any());diff=bool((z5.objective_key!=cur.objective_key).any())
    if same!=diff:q=cur.to_dict();q['future_different_objective_5s']=int(diff);fut.append(q)
   if cur.objective_family=='PAIR_BALANCE':
    pri=g[(g.t<t)&(g.t>=t-H15)&(g.objective_key==cur.objective_key)];paid=0.;roots=0
    for _,pr in pri.drop_duplicates('order_hash').iterrows():
     q=confirmed(idx,mid,pr.order_hash,t);paid+=q;roots+=int(q>EPS)
    z=cur.to_dict();z.update({'prior_same_objective_roots_15s':int(pri.order_hash.nunique()),'prior_same_objective_paid_roots':roots,'prior_same_objective_confirmed_qty':paid,'preposition_like_same_objective':int(paid>EPS),'same_objective_live_overlap':int(cur.weak_active_roots>0),'same_objective_reserved_total':float(cur.current_commitment+cur.weak_unresolved_shares),'reservation_over_current_deficit':float((cur.current_commitment+cur.weak_unresolved_shares)/max(cur.abs_gap,EPS)),'prior_paid_over_current_deficit':float(paid/max(cur.abs_gap,EPS))});pre.append(z)
   if cur.objective_family=='STATE_SHAPING':
    pri=g[(g.t<t)&(g.t>=t-H15)&(g.objective_family=='PAIR_BALANCE')];z=cur.to_dict();z.update({'recent_pair_balance_roots_15s':int(pri.order_hash.nunique()),'state_shaping_recent_parallel':int(len(pri)>0),'state_shaping_live_parallel':int(cur.weak_active_roots>0)});state.append(z)
  end=int(g.t.max())
  for k,v in open_last.items():
   if end-v>H15:lc['OBSERVED_MEMORY_RETIRE']+=1;lm['OBSERVED_MEMORY_RETIRE'].add(int(mid))
 predf=pd.DataFrame(pre);statedf=pd.DataFrame(state);futdf=pd.DataFrame(fut);predf.to_csv(PRE,index=False);statedf.to_csv(STATE,index=False);futdf.to_csv(FUT,index=False)
 pp=predf[predf.preposition_like_same_objective==1];sp=statedf[statedf.state_shaping_recent_parallel==1];ss=statedf[statedf.state_shaping_recent_parallel==0]
 anatomy={c:{'parallelMedian':float(sp[c].median()) if len(sp) else None,'soloMedian':float(ss[c].median()) if len(ss) else None,'parallelGreaterProbability':psup(sp[c].dropna(),ss[c].dropna())} for c in GEOM+MEM+['weak_unresolved_shares','weak_progress_ratio','weak_fill_shares_5s']}
 joint=pd.read_csv(ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1_rows.csv').replace([np.inf,-np.inf],np.nan);joint['flow_context']=np.select([(joint.weak_maker_shares_15s>0)&(joint.surplus_maker_shares_15s>0),(joint.weak_maker_shares_15s>0),(joint.surplus_maker_shares_15s>0)],['DUAL_WEAK_SURPLUS','WEAK_ONLY','SURPLUS_ONLY'],default='NONE');jc={str(k):{'rows':int(len(x)),'markets':int(x.market_id.nunique()),'jointOutcomeRate':float(x.y_joint.mean()),'floorProgressRate':float(x.y_floor.mean()),'upsideProgressRate':float(x.y_upside.mean())} for k,x in joint.groupby('flow_context')};dual=jc.get('DUAL_WEAK_SURPLUS',{}).get('jointOutcomeRate');none=jc.get('NONE',{}).get('jointOutcomeRate')
 art={'coverage':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'familySupport':{str(k):{'rows':int(len(x)),'markets':int(x.market_id.nunique())} for k,x in d.groupby('objective_family')}},'sameVsDifferentObjective':{'rows':int(pair['rows']),'sameObjectivePairs':int(pair['same']),'differentObjectivePairs':int(pair['diff']),'sameSidePairs':int(pair['sameSide']),'sameSideSameObjective':int(pair['sameSideSame']),'sameSideDifferentObjective':int(pair['sameSideDiff']),'sameTimestampDirectedPairs':int(sim['rows']),'sameTimestampSameObjective':int(sim['same']),'sameTimestampDifferentObjective':int(sim['diff'])},'prepositionLikeSameObjective':{'pairBalanceOpenings':int(len(predf)),'prepositionLikeSameObjective':int(len(pp)),'rate':float(len(pp)/len(predf)),'markets':int(pp.market_id.nunique()),'liveOverlapAmongPrepositionRate':float(pp.same_objective_live_overlap.mean()) if len(pp) else None,'medianPriorConfirmedQty':float(pp.prior_same_objective_confirmed_qty.median()) if len(pp) else None,'medianPriorPaidOverCurrentDeficit':float(pp.prior_paid_over_current_deficit.median()) if len(pp) else None,'reservationOverCurrentDeficitRate':float((pp.reservation_over_current_deficit>1+1e-9).mean()) if len(pp) else None,'medianReservationOverCurrentDeficit':float(pp.reservation_over_current_deficit.median()) if len(pp) else None},'stateShapingParallel':{'stateShapingOpenings':int(len(statedf)),'recentPairBalanceParallel':int(len(sp)),'recentParallelRate':float(len(sp)/len(statedf)),'recentParallelMarkets':int(sp.market_id.nunique()),'livePairBalanceParallel':int(statedf.state_shaping_live_parallel.sum()),'liveParallelRate':float(statedf.state_shaping_live_parallel.mean()),'liveParallelMarkets':int(statedf.loc[statedf.state_shaping_live_parallel==1,'market_id'].nunique()),'strictPastAntecedentAnatomy':anatomy},'futureRows':int(len(futdf)),'objectiveGroupLifecycleObservation':{'counts':dict(lc),'markets':{k:len(v) for k,v in lm.items()}},'independentJointBaseUpsideReplication':{'rows':int(len(joint)),'markets':int(joint.market_id.nunique()),'contexts':jc,'dualVsNoneJointRateRatio':float(dual/none) if dual is not None and none not in (None,0) else None}}
 dump(STRUCT,art);print(json.dumps(art,ensure_ascii=False))
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=seed)
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def chrono(path,label,sets,seed):
 x=pd.read_csv(path).dropna(subset=[label]).copy();x[label]=x[label].astype(int);order=x.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(40,int(len(order)*.60));initial=min(initial,len(order)-4);rem=len(order)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;bs=[]
 for bi,sz in enumerate(sizes,1):
  trm=set(order[:cur]);tem=set(order[cur:cur+sz]);cur+=sz;tr=x[x.market_id.isin(trm)];te=x[x.market_id.isin(tem)];b={'block':bi}
  for j,(name,feats) in enumerate(sets.items()):
   use=[c for c in feats if c in x.columns];a=tr.dropna(subset=use+[label]);z=te.dropna(subset=use+[label])
   if len(a)<100 or len(z)<20 or a[label].nunique()<2 or z[label].nunique()<2:b[name]={'n':int(len(z)),'auc':None};continue
   m=hgb(seed+bi*20+j).fit(a[use],a[label]);b[name]=metrics(z[label],m.predict_proba(z[use])[:,1])
  bs.append(b)
 s={}
 for name in sets:
  q=[b[name] for b in bs if b[name].get('auc') is not None];s[name]={'blocks':len(q),'meanAuc':float(np.mean([z['auc'] for z in q])) if q else None,'worstAuc':float(np.min([z['auc'] for z in q])) if q else None,'meanAp':float(np.mean([z['ap'] for z in q])) if q else None,'meanLogLoss':float(np.mean([z['logLoss'] for z in q])) if q else None}
 return {'coverage':{'rows':int(len(x)),'markets':len(order),'rate':float(x[label].mean())},'blocks':bs,'summary':s}
def phase_model(which):
 p=MODELS;all=json.loads(p.read_text()) if p.exists() else {}
 if which=='pre':all['preposition']=chrono(PRE,'preposition_like_same_objective',{'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_MEMORY_OWNER':GEOM+MEM+OWNER,'FULL_ROLE_MEMORY':GEOM+MEM+OWNER+ROLEMEM},39000)
 elif which=='state':all['state']=chrono(STATE,'state_shaping_live_parallel',{'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_ROLE_MEMORY':GEOM+MEM+ROLEMEM,'FULL_OWNER_PROGRESS':GEOM+MEM+ROLEMEM+OWNER},40000)
 elif which=='future':all['future']=chrono(FUT,'future_different_objective_5s',{'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_MEMORY_OWNER':GEOM+MEM+OWNER,'FULL_WITH_OBJECTIVE_CONTEXT':GEOM+MEM+OWNER+ROLEMEM+CURR},38000)
 dump(p,all);print(json.dumps(all[which],ensure_ascii=False))
def deltas(res):
 s=res.get('summary',{});base=s.get('GEOMETRY',{});o={}
 for k,q in s.items():
  if k=='GEOMETRY':continue
  o[k]={'meanAucDeltaVsGeometry':q.get('meanAuc')-base.get('meanAuc') if q.get('meanAuc') is not None and base.get('meanAuc') is not None else None,'worstAucDeltaVsGeometry':q.get('worstAuc')-base.get('worstAuc') if q.get('worstAuc') is not None and base.get('worstAuc') is not None else None,'logLossImprovementVsGeometry':base.get('meanLogLoss')-q.get('meanLogLoss') if q.get('meanLogLoss') is not None and base.get('meanLogLoss') is not None else None}
 return o
def phase_final():
 st=json.loads(STRUCT.read_text());mo=json.loads(MODELS.read_text());sv=st['sameVsDifferentObjective'];pp=st['prepositionLikeSameObjective'];ss=st['stateShapingParallel'];jc=st['independentJointBaseUpsideReplication'];checks={'recurringSamePurpose':pp['prepositionLikeSameObjective']>=20 and sv['sameObjectivePairs']>=100,'recurringDifferentPurposeParallel':ss['recentPairBalanceParallel']>=20 and sv['differentObjectivePairs']>=100,'sameSideIdentityInsufficient':sv['sameSideDifferentObjective']>0,'dualWeakSurplusEconomicSupport':jc['contexts'].get('DUAL_WEAK_SURPLUS',{}).get('rows',0)>0};status='KEEP' if all(checks.values()) else 'INCONCLUSIVE'
 rep={'version':'R4_P0B_TARGET_OBJECTIVE_TOPOLOGY_V2','lane':'E_TARGET_OBJECTIVE_TOPOLOGY','status':status,'researchOnly':True,'actionAuthority':False,'preRegistration':'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preregistered_v2.json','boundaryPreflight':'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_boundary_preflight_v1.json','coverage':st['coverage'],'sameVsDifferentObjective':sv,'prepositionLikeSameObjective':{'structural':pp,'narrowTeacher':mo['preposition'],'featureGroupDeltas':deltas(mo['preposition'])},'stateShapingParallel':{'structural':{k:v for k,v in ss.items() if k!='strictPastAntecedentAnatomy'},'strictPastAntecedentAnatomy':ss['strictPastAntecedentAnatomy'],'liveOverlapTeacher':mo['state'],'featureGroupDeltas':deltas(mo['state'])},'futureObjectiveTopology5s':{'narrowTeacher':mo['future'],'featureGroupDeltas':deltas(mo['future'])},'objectiveGroupLifecycleObservation':st['objectiveGroupLifecycleObservation'],'independentJointBaseUpsideReplication':jc,'decisionChecks':checks,'mainObjectiveLedgerTransfer':{'groupKeyCandidate':'objective_family + side, above responsibility_id','pairBalanceCreditRuleCandidate':'Confirmed/remaining quantity from earlier same-objective PAIR_BALANCE roots belongs to one objective budget and must be credited/reserved at group level before admitting later same-purpose work.','stateShapingRuleCandidate':'STATE_SHAPING/ADD is a distinct objective family; do not consume its quantity using generic pair-balance credit merely because side matches after an orientation change.','requiredRuntimeInputs':['strict-past weak/dominant relation','portfolio deficit/absNet','responsibility family/purpose','active root ownership','confirmed and unresolved quantity','objective-group memory/age']},'boundaryCompliance':{'r3Changed':False,'8781Changed':False,'echtgeldChanged':False,'mainSuccessorSimulatorChanged':False,'dreamFillUsed':False,'sealed20260816UsedForScoring':False,'runtimeAuthorityAdded':False,'thresholdSweepUsed':False,'pnlOptimizationUsed':False,'parallelAsSerialHandoffPositive':False},'filesChanged':['tools/test_r4_p0b_target_objective_topology_v1.py','tools/test_r4_p0b_target_objective_topology_v2.py','tools/test_r4_p0b_target_objective_topology_finalize_v2.py','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preregistered_v1.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_boundary_preflight_v1.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preregistered_v2.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_rows_v2.csv','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_structural_v2.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preposition_rows_v2.csv','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_state_rows_v2.csv','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_future_rows_v2.csv','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_models_v2.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_v2.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_lane_status_v2.json']};dump(OUT,rep);dump(STATUS,{'version':'R4_P0B_TARGET_OBJECTIVE_TOPOLOGY_LANE_STATUS_V2','lane':'E_TARGET_OBJECTIVE_TOPOLOGY','status':status,'researchOnly':True,'actionAuthority':False,'mainArtifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'decisionChecks':checks,'boundaryCompliance':rep['boundaryCompliance']});print(json.dumps({'status':status,'checks':checks},ensure_ascii=False))
if __name__=='__main__':
 cmd=sys.argv[1]; {'struct':phase_struct,'pre':lambda:phase_model('pre'),'state':lambda:phase_model('state'),'future':lambda:phase_model('future'),'final':phase_final}[cmd]()
