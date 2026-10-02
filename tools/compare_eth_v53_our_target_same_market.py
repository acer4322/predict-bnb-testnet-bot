from __future__ import annotations
import argparse,json,glob,sqlite3,zipfile,statistics
from pathlib import Path
EPS=1e-9
ROLES=['PASSIVE_BASE_MM','PASSIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_REPAIR','ACTIVE_EXPAND','ACTIVE_OTHER']

def cycle_stats(events):
 toks=[x['role'] for x in events];rep={'PASSIVE_REPAIR','ACTIVE_REPAIR'};exp={'PASSIVE_EXPAND','ACTIVE_EXPAND'};idx=[i for i,x in enumerate(toks) if x in exp];rer=strict=exp_rep=0
 comp=[]
 for x in toks:
  if not comp or comp[-1]!=x:comp.append(x)
 for j,i in enumerate(idx):
  lo=(idx[j-1]+1) if j else 0;hi=idx[j+1] if j+1<len(idx) else len(toks);pre=toks[lo:i];post=toks[i+1:hi]
  if any(x in rep for x in pre) and any(x in rep for x in post):rer+=1
  if 'PASSIVE_REPAIR' in pre and 'ACTIVE_REPAIR' in post:strict+=1
  if any(x in rep for x in post):exp_rep+=1
 return {'compressedSequence':comp,'repairExpandRepairRounds':rer,'passiveRepairExpandActiveRepairRounds':strict,'expandThenRepairRounds':exp_rep}

def target_events(rows,cutoff=None):
 u=d=0.0;events=[]
 for mid,role,side,p,q,t in rows:
  if cutoff is not None and int(t)>int(cutoff):continue
  role=str(role);side=str(side);p=float(p or 0);q=float(q or 0);t=int(t)
  gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  passive=(role=='MAKER')
  if weak is None:
   rr='PASSIVE_BASE_MM' if passive else 'ACTIVE_OTHER';events.append({'t':t,'role':rr,'qty':q,'side':side,'price':p})
  elif side==weak:
   rq=min(q,gap);xq=max(0.0,q-rq)
   if rq>EPS:events.append({'t':t,'role':'PASSIVE_REPAIR' if passive else 'ACTIVE_REPAIR','qty':rq,'side':side,'price':p})
   if xq>EPS:events.append({'t':t,'role':'PASSIVE_EXPAND' if passive else 'ACTIVE_EXPAND','qty':xq,'side':side,'price':p})
  else:
   events.append({'t':t,'role':'PASSIVE_EXPAND' if passive else 'ACTIVE_EXPAND','qty':q,'side':side,'price':p})
  if side=='UP':u+=q
  else:d+=q
 return events

def summarize_events(events):
 c={};q={}
 for e in events:c[e['role']]=c.get(e['role'],0)+1;q[e['role']]=q.get(e['role'],0.0)+float(e['qty'])
 return {'roleCounts':c,'roleQty':q,**cycle_stats(events),'makerEvents':sum(v for k,v in c.items() if k.startswith('PASSIVE_')),'activeEvents':sum(v for k,v in c.items() if k.startswith('ACTIVE_'))}

def maxdd(vals):
 eq=0.;peak=0.;dd=0.
 for x in vals:eq+=x;peak=max(peak,eq);dd=max(dd,peak-eq)
 return dd

def pnlstats(vals):
 if not vals:return {}
 return {'sum':sum(vals),'wins':sum(x>0 for x in vals),'winRate':sum(x>0 for x in vals)/len(vals),'maxProfit':max(vals),'maxLoss':min(vals),'median':statistics.median(vals),'maxDrawdown':maxdd(vals)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=sorted(glob.glob(a.pattern));ours=[]
 if not src:raise SystemExit('no V53 files')
 for f in src:ours.extend(json.load(open(f,encoding='utf-8')).get('rows') or [])
 ours=sorted(ours,key=lambda x:int(x['marketId']));mids=[int(x['marketId']) for x in ours]
 co={int(x['marketId']):x for x in json.loads(zipfile.ZipFile(a.bundle).read('cohort.json'))['rows']}
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(mids));rr=con.execute(f"select market_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();by={m:[] for m in mids}
 for r in rr:by[int(r[0])].append(r)
 per=[];ourp=[];tarp=[];scoring=[];all_oc={};all_tc={};all_tpre={};our_round=tar_round=our_strict=tar_strict=0
 for x in ours:
  m=int(x['marketId']);f=x['functional'];winner=str(x['winner']).upper();trows=by.get(m,[]);tev=target_events(trows);pre=target_events(trows,int(x.get('windowEndMs') or co[m].get('windowEndMs'))-180000);ts=summarize_events(tev);tps=summarize_events(pre);oe=f.get('v53FillEvents',[]);os=summarize_events(oe)
  inv={'UP':0.0,'DOWN':0.0};cost=0.0
  for _,_,s,p,q,_ in trows:inv[str(s).upper()]+=float(q or 0);cost+=float(p or 0)*float(q or 0)
  tp=float(inv[winner]-cost);op=float(f.get('pnlDiagnosticOnly') or 0.0);sp=float(x.get('targetPnlScoringOnly') or co[m].get('targetPnlScoringOnly') or 0.0)
  ourp.append(op);tarp.append(tp);scoring.append(sp)
  for k,v in os['roleCounts'].items():all_oc[k]=all_oc.get(k,0)+v
  for k,v in ts['roleCounts'].items():all_tc[k]=all_tc.get(k,0)+v
  for k,v in tps['roleCounts'].items():all_tpre[k]=all_tpre.get(k,0)+v
  our_round+=os['repairExpandRepairRounds'];tar_round+=ts['repairExpandRepairRounds'];our_strict+=os['passiveRepairExpandActiveRepairRounds'];tar_strict+=ts['passiveRepairExpandActiveRepairRounds']
  per.append({'marketId':m,'winner':winner,'ourPnl':op,'targetPnlRecomputed':tp,'targetPnlScoringOnly':sp,'pnlGapOurMinusTarget':op-tp,'our':os,'targetFull':ts,'targetPre180':tps,'roleCountDiffFull':{k:os['roleCounts'].get(k,0)-ts['roleCounts'].get(k,0) for k in ROLES},'roleCountDiffPre180':{k:os['roleCounts'].get(k,0)-tps['roleCounts'].get(k,0) for k in ROLES}})
 gaps=[ourp[i]-tarp[i] for i in range(len(ourp))];pnl_cons=[abs(tarp[i]-scoring[i]) for i in range(len(tarp))]
 agg={'markets':len(ours),'targetRows':len(rr),'ourRoleCounts':all_oc,'targetRoleCountsFull':all_tc,'targetRoleCountsPre180':all_tpre,'roleCountDiffFull':{k:all_oc.get(k,0)-all_tc.get(k,0) for k in ROLES},'roleCountDiffPre180':{k:all_oc.get(k,0)-all_tpre.get(k,0) for k in ROLES},'ourRepairExpandRepairRounds':our_round,'targetRepairExpandRepairRounds':tar_round,'ourStrictFullRounds':our_strict,'targetStrictFullRounds':tar_strict,'ourMarketsWith2PlusRounds':sum(y['our']['repairExpandRepairRounds']>=2 for y in per),'targetMarketsWith2PlusRounds':sum(y['targetFull']['repairExpandRepairRounds']>=2 for y in per),'pnl':{'our':pnlstats(ourp),'target':pnlstats(tarp),'gap':pnlstats(gaps)},'targetPnlRecomputeVsCohort':{'meanAbsDiff':sum(pnl_cons)/len(pnl_cons) if pnl_cons else None,'maxAbsDiff':max(pnl_cons) if pnl_cons else None}}
 top=sorted(per,key=lambda z:(-z['our']['repairExpandRepairRounds'],-z['our']['passiveRepairExpandActiveRepairRounds'],z['marketId']))[:20]
 out={'version':'ETH_V53_OUR_TARGET_SAME_MARKET_MULTICYCLE_COMPARISON','sameMarket':True,'targetSource':'target_wallet_official_v1.db actual parent fills','ourSource':'V53 realistic-HFT actual carrier fills','aggregate':agg,'topOurCycleMarkets':top,'perMarket':per,'boundary':['Target role reconstructed from strict-past inventory; crossing weak-side fill split Repair then Expand','Target full-market counts plus <=120s? targetPre180 means fills at or before windowEnd-180s','OUR PnL and Target PnL use binary winner shares minus buy cost; no fee normalization','No action-count threshold fitting']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'topOurCycleMarkets':[{'marketId':z['marketId'],'ourRounds':z['our']['repairExpandRepairRounds'],'strict':z['our']['passiveRepairExpandActiveRepairRounds'],'ourPnl':z['ourPnl'],'targetPnl':z['targetPnlRecomputed'],'ourSeq':z['our']['compressedSequence'][:20],'targetSeq':z['targetFull']['compressedSequence'][:20]} for z in top[:10]]},ensure_ascii=False))
if __name__=='__main__':main()
