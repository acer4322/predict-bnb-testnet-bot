from __future__ import annotations
import argparse,bisect,importlib.util,json,math,statistics
from pathlib import Path
from collections import defaultdict
import numpy as np,joblib
from sklearn.metrics import roc_auc_score

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('teach',HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py');teach=importlib.util.module_from_spec(spec);spec.loader.exec_module(teach)
core=teach.core
spec2=importlib.util.spec_from_file_location('bookbase',HERE/'train_target_eth_first_leg_package_path_stability_teacher_v1.py');bookbase=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(bookbase)
EPS=1e-9
FEATURES=list(teach.FEATURES)

RAW_DEFAULT=[
 'data/research/lan_worker_returns/eth-v23-unseen20-c0/result.json',
 'data/research/lan_worker_returns/eth-v23-unseen20-c1/result.json',
 'data/research/lan_worker_returns/eth-v23-unseen20-c2/result.json',
 'data/research/lan_worker_returns/eth-v23-unseen20-c3/result.json',
 'data/research/lan_worker_returns/eth-v23-confirm20-c0-v2/result.json',
 'data/research/lan_worker_returns/eth-v23-confirm20-c1-v2/result.json',
 'data/research/lan_worker_returns/eth-v23-confirm20-c2-v2/result.json',
 'data/research/lan_worker_returns/eth-v23-confirm20-c3-v2/result.json',
]

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def load_raw(paths):
 out={};dups=[]
 for p in paths:
  pp=Path(p)
  if not pp.exists():continue
  d=json.load(open(pp,encoding='utf-8'))
  for r in d.get('rows',[]):
   mid=int(r['marketId'])
   if mid in out:dups.append(mid)
   out[mid]=r
 return out,dups

def annotate_fills(v23):
 ca=v23['causal'];subs=[dict(x) for x in ca.get('submitTrace',[])];fills=[dict(x) for x in ca.get('fillTrace',[])];out=[];up=down=0.0
 for fi,f in enumerate(sorted(fills,key=lambda x:int(x['t']))):
  t=int(f['t']);side=str(f['side']);px=float(f['price']);qty=float(f['qty'])
  cand=[s for s in subs if int(s['t'])<=t and str(s['side'])==side and abs(float(s['price'])-px)<=1e-8]
  s=max(cand,key=lambda x:int(x['t'])) if cand else None
  pre=abs(up-down)
  if side=='UP':up+=qty
  else:down+=qty
  post=abs(up-down);delta=post-pre
  lane=s.get('lane') if s else None;pid=s.get('parentId') if s else None
  if pid is not None:key=f"P:{pid}"
  elif lane=='RESERVE_BUILD_FIRST':key=f"RBF:{int(s['t'])}" if s else f"F:{fi}"
  elif s is not None:key=f"S:{int(s['t'])}"
  else:key=f"F:{fi}"
  out.append({'event_ms':t,'role':'MAKER','side':side,'price':px,'shares':qty,'lane':lane,'parentId':pid,'parentKey':key,'preMakerAbsNet':pre,'postMakerAbsNet':post,'deltaMakerAbsNet':delta,'matchedSubmitAt':int(s['t']) if s else None})
 return out

def make_inventory(events,cp):
 inv=core.Inventory()
 for e in events:
  if int(e['event_ms'])>cp:break
  inv.apply({'event_ms':int(e['event_ms']),'role':'MAKER','side':str(e['side']),'price':float(e['price']),'shares':float(e['shares'])})
 return inv

def parent_history(events,cp):
 by={}
 for e in events:
  if int(e['event_ms'])>cp:break
  k=e['parentKey'];z=by.setdefault(k,{'key':k,'firstEventMs':int(e['event_ms']),'lastEventMs':int(e['event_ms']),'shares':0.0,'preMakerAbsNet':float(e['preMakerAbsNet']),'postMakerAbsNet':float(e['postMakerAbsNet']),'deltaMakerAbsNet':0.0,'lane':e.get('lane')})
  z['firstEventMs']=min(z['firstEventMs'],int(e['event_ms']));z['lastEventMs']=max(z['lastEventMs'],int(e['event_ms']));z['shares']+=float(e['shares']);z['postMakerAbsNet']=float(e['postMakerAbsNet']);z['deltaMakerAbsNet']=z['postMakerAbsNet']-z['preMakerAbsNet']
 return sorted(by.values(),key=lambda z:z['firstEventMs'])

def feature_row(inv,parents,cp,mend,state):
 f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None;bf=core.outcome_book({'bids':state[1],'asks':state[2]},dom)
 if bf is None:return None
 extra={}
 m3=[e for e in inv.events if e['role']=='MAKER' and cp-3000<int(e['event_ms'])<=cp];extra['maker_fills_3s']=float(len(m3));extra['maker_shares_3s']=float(sum(float(e['shares']) for e in m3))
 for w in (1000,3000,5000,10000):
  rp=[p for p in parents if p['deltaMakerAbsNet']<-EPS and p['lastEventMs']<=cp and p['lastEventMs']>cp-w]
  extra[f'maker_repair_parents_{w//1000}s']=float(len(rp));extra[f'maker_repair_shares_{w//1000}s']=float(sum(float(p['shares']) for p in rp))
 ex=[p for p in parents if p['deltaMakerAbsNet']>EPS and p['firstEventMs']<=cp];ex=max(ex,key=lambda p:p['firstEventMs']) if ex else None
 if ex is None:
  extra['latest_maker_expansion_age_ms']=math.nan;extra['latest_maker_expansion_repaid_frac']=math.nan
 else:
  extra['latest_maker_expansion_age_ms']=float(cp-ex['firstEventMs']);start=max(float(ex['postMakerAbsNet'])-float(ex['preMakerAbsNet']),EPS);cur=float(f['maker_abs_net']);extra['latest_maker_expansion_repaid_frac']=float((float(ex['postMakerAbsNet'])-cur)/start)
 vals={'seconds_left':(mend-cp)/1000.0,**f,**bf,**extra}
 x=np.asarray([np.nan if vals.get(k) is None else float(vals.get(k)) for k in FEATURES],np.float32)
 return x,vals

def half_offset_points(mstart,lo,hi):
 base=mstart+500
 k=max(0,int(math.floor((lo-base)/1000))+1)
 cp=base+k*1000;out=[]
 while cp<hi:
  if cp>lo:out.append(cp)
  cp+=1000
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offline',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--model',required=True);ap.add_argument('--raw',nargs='*',default=RAW_DEFAULT);ap.add_argument('--output',required=True);a=ap.parse_args()
 off=json.load(open(a.offline,encoding='utf-8'));raw,dups=load_raw(a.raw);art=joblib.load(a.model);model=art['model'];assert list(art['features'])==FEATURES
 import sqlite3;c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True)
 bymid=defaultdict(list)
 for r in off['rows']:bymid[int(r['marketId'])].append(r)
 rows=[];match=[]
 try:
  for mid,cycles in sorted(bymid.items()):
   rr=raw.get(mid);match.append({'marketId':mid,'rawFound':rr is not None,'cycles':len(cycles)})
   if rr is None:continue
   events=annotate_fills(rr['V23']);meta=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
   if meta is None:continue
   mend=int(meta[0]);mstart=mend-300000;cycles=sorted(cycles,key=lambda z:int(z['firstFillAt']))
   lo=min(int(z['firstFillAt']) for z in cycles)-2000;hi=max(int(z['packageRepairFillAt'] or (int(z['firstFillAt'])+30000)) for z in cycles)+1000;states=bookbase.load_states(c,mid,lo,hi,'received_at_ms');ts=[s[0] for s in states]
   for j,cy in enumerate(cycles):
    start=int(cy['firstFillAt']);next_first=int(cycles[j+1]['firstFillAt']) if j+1<len(cycles) else 10**30
    if cy.get('completedWithin30sByTrace') and cy.get('packageRepairFillAt') is not None:end=int(cy['packageRepairFillAt'])
    else:end=min(start+30000,next_first)
    cps=half_offset_points(mstart,start,end);ps=[];state_rows=[]
    for cp in cps:
     idx=bisect.bisect_right(ts,cp)-1
     if idx<0:continue
     st=states[idx];book_age=cp-int(st[0])
     if book_age<0 or book_age>2000:continue
     inv=make_inventory(events,cp);ph=parent_history(events,cp);z=feature_row(inv,ph,cp,mend,st)
     if z is None:continue
     x,vals=z;p=float(model.predict_proba(x.reshape(1,-1))[0,1]);ps.append(p);state_rows.append({'t':cp,'bookAgeMs':book_age,'pRepairTaker1s':p,'makerAbsNet':vals['maker_abs_net'],'makerPairedCoverage':vals['maker_paired_coverage'],'worstCaseFloor':vals['worst_case_floor'],'lastMakerAgeMs':vals['last_maker_age_ms'],'makerRepairParents3s':vals['maker_repair_parents_3s'],'latestMakerExpansionAgeMs':vals['latest_maker_expansion_age_ms'],'latestMakerExpansionRepaidFrac':vals['latest_maker_expansion_repaid_frac']})
    warn=next((x['t'] for x in state_rows if x['pRepairTaker1s']>=.5),None);mx=max(ps) if ps else None
    rows.append({'marketId':mid,'cycleIndex':int(cy['cycleIndex']),'firstFillAt':start,'windowEndAt':end,'windowMs':end-start,'completedWithin30s':bool(cy['completedWithin30sByTrace']),'eventuallyCompleted':bool(cy['eventuallyCompletedByTrace']),'packageRepairFillAt':cy.get('packageRepairFillAt'),'maxHazard':mx,'medianHazard':statistics.median(ps) if ps else None,'meanHazard':statistics.mean(ps) if ps else None,'warningAt':warn,'warningLeadToEndMs':None if warn is None else end-int(warn),'warningRateState':sum(p>=.5 for p in ps)/len(ps) if ps else None,'stateRows':state_rows,'queueEvidence':{'firstLegalBehindTicks':cy.get('firstLegalBehindTicks'),'maxLegalBehindTicks':cy.get('maxLegalBehindTicks'),'within1ReceiptFraction':cy.get('within1ReceiptFraction'),'firstToFar5Ms':cy.get('firstToFar5Ms')}})
 finally:c.close()
 valid=[r for r in rows if r['maxHazard'] is not None];fail=[r for r in valid if not r['completedWithin30s']];comp=[r for r in valid if r['completedWithin30s']];y=np.asarray([0 if r['completedWithin30s'] else 1 for r in valid],int);p=np.asarray([r['maxHazard'] for r in valid],float)
 def rate(rr,pred):return sum(bool(pred(r)) for r in rr)/len(rr) if rr else None
 auc=float(roc_auc_score(y,p)) if len(set(y))>1 else None
 out={'version':'ETH_V23_REPAIR_TAKER_HAZARD_BRIDGE_V1','researchOnly':True,'coverage':{'offlineCycles':len(off['rows']),'rawMarkets':len(raw),'matchedCycles':len(rows),'scoredCycles':len(valid),'completed30s':len(comp),'failed30s':len(fail),'duplicateRawMarketIds':sorted(set(dups))},'rawMatchAudit':match,'metrics':{'failureAucFromMaxHazard':auc,'completedMaxHazard':stats([r['maxHazard'] for r in comp]),'failedMaxHazard':stats([r['maxHazard'] for r in fail]),'completedMedianHazard':stats([r['medianHazard'] for r in comp]),'failedMedianHazard':stats([r['medianHazard'] for r in fail]),'completedWarningRateAt05':rate(comp,lambda r:r['warningAt'] is not None),'failedWarningRateAt05':rate(fail,lambda r:r['warningAt'] is not None),'completedWarningLeadMs':stats([r['warningLeadToEndMs'] for r in comp]),'failedWarningLeadMs':stats([r['warningLeadToEndMs'] for r in fail])},'rows':rows,'boundary':['Frozen ETH Target 1s REPAIR-Taker hazard only; no refit or threshold sweep.','OUR V23 is Maker-only, so Taker channels are zero/NaN by construction.','Public book uses received_at_ms receipt clock; checkpoints preserve teacher half-offset 1s grid.','Completion labels are evaluation only; no active Taker authority is granted.']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'metrics':out['metrics'],'top':sorted([{k:r[k] for k in ('marketId','cycleIndex','completedWithin30s','maxHazard','medianHazard','warningLeadToEndMs')} for r in valid],key=lambda x:x['maxHazard'],reverse=True)},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
