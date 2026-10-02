from __future__ import annotations
import argparse,bisect,glob,json,math,statistics,sqlite3,importlib.util
from pathlib import Path
from collections import defaultdict
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bookbase',HERE/'train_target_eth_first_leg_package_path_stability_teacher_v1.py');bookbase=importlib.util.module_from_spec(spec);spec.loader.exec_module(bookbase)
EPS=1e-9

def load_v23():
 out={}
 for p in glob.glob('data/research/lan_worker_returns/eth-v23-unseen20-c*/result.json')+glob.glob('data/research/lan_worker_returns/eth-v23-confirm20-c*-v2/result.json'):
  d=json.load(open(p,encoding='utf-8'))
  for r in d.get('rows',[]):out[int(r['marketId'])]=r['V23']
 return out

def load_v27():
 out={}
 for p in glob.glob('data/research/lan_worker_returns/eth-v27-activeonly-*/result.json'):
  d=json.load(open(p,encoding='utf-8'))
  for r in d.get('rows',[]):
   f=r['functional'];arm=next((e for e in f.get('activeTrace',[]) if e.get('event')=='ACTIVE_ARM'),None);sub=next((e for e in f.get('activeTrace',[]) if e.get('event')=='ACTIVE_SUBMIT'),None)
   if arm and sub:out[int(r['marketId'])]={'functional':f,'arm':arm,'submit':sub}
 return out

def level_depth(bids,asks,side,p):
 if side=='UP': return float(bids.get(round(float(p),12),bids.get(float(p),0.0)))
 np0=1.0-float(p);return float(asks.get(round(np0,12),asks.get(np0,0.0)))

def qstats(xs):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return {'n':0}
 return {'n':len(ys),'mean':statistics.mean(ys),'median':statistics.median(ys),'min':min(ys),'max':max(ys)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offline',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--output',required=True);a=ap.parse_args()
 off=json.load(open(a.offline,encoding='utf-8'));byoff=defaultdict(list)
 for r in off['rows']:byoff[int(r['marketId'])].append(r)
 v23=load_v23();v27=load_v27();c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True);rows=[]
 try:
  for mid,z in sorted(v27.items()):
   arm=int(z['arm']['t']);repair_side=str(z['arm']['side']);base=v23[mid];cycles=[r for r in byoff[mid] if int(r['firstFillAt'])<arm]
   if not cycles:continue
   cy=max(cycles,key=lambda r:int(r['firstFillAt']));first_t=int(cy['firstFillAt']);first_price=float(cy['firstPrice'])
   subs=[s for s in base['causal'].get('submitTrace',[]) if s.get('lane')=='REPAIR' and str(s.get('side'))==repair_side and int(s.get('t'))<=arm and int(s.get('t'))>=first_t]
   carrier=max(subs,key=lambda s:int(s['t'])) if subs else None
   if carrier is None:
    rows.append({'marketId':mid,'cycleIndex':int(cy['cycleIndex']),'armAt':arm,'completedWithin30s':bool(cy['completedWithin30sByTrace']),'eventuallyCompleted':bool(cy['eventuallyCompletedByTrace']),'carrierFound':False});continue
   placed=int(carrier['t']);price=float(carrier['price']);states=bookbase.load_states(c,mid,placed-100,arm+1,'received_at_ms');win=[s for s in states if placed<=int(s[0])<=arm]
   prev=None;dec=0;zero=False;within=[];depths=[];lastbb=lastask=None
   for _,bids,asks in win:
    sb=bookbase.side_book(bids,asks,repair_side,price)
    if sb is None:continue
    d=float(sb['levelDepth'] or 0.0);depths.append(d);within.append(max(0.0,(float(sb['bid'])-price)/.01)<=1+EPS);zero=zero or d<=EPS
    if prev is not None and d<prev-EPS:dec+=1
    prev=d;lastbb=float(sb['bid']);lastask=float(sb['ask'])
   place_depth=depths[0] if depths else None;arm_depth=depths[-1] if depths else None;behind=None if lastbb is None else max(0.0,(lastbb-price)/.01);pair=None if lastask is None else first_price+lastask
   rows.append({'marketId':mid,'cycleIndex':int(cy['cycleIndex']),'armAt':arm,'activeSubmitAt':int(z['submit']['t']),'activeHazardAtArm':float(z['arm']['hazard']),'completedWithin30s':bool(cy['completedWithin30sByTrace']),'eventuallyCompleted':bool(cy['eventuallyCompletedByTrace']),'carrierFound':True,'carrierPlacedAt':placed,'carrierAgeAtArmMs':arm-placed,'carrierPrice':price,'carrierBehindBestTicksAtArm':behind,'samePriceDepthAtPlacement':place_depth,'samePriceDepthAtArm':arm_depth,'strictPastDepthDecreaseEvents':dec,'strictPastLevelZeroSeen':zero,'within1ReceiptFractionBeforeArm':sum(within)/len(within) if within else None,'receiptStates':len(within),'firstPrice':first_price,'repairBestAskAtArm':lastask,'pairSumAtArmAsk':pair,'baselineTerminalAbsNet':float(base['functional'].get('absNet') or 0),'baselineCycles':float(base['functional'].get('reserveCycleCompletion') or 0)})
 finally:c.close()
 comp=[r for r in rows if r.get('completedWithin30s')];fail=[r for r in rows if not r.get('completedWithin30s')]
 def block(rr):return {'n':len(rr),'carrierAgeMs':qstats([r.get('carrierAgeAtArmMs') for r in rr]),'behindTicks':qstats([r.get('carrierBehindBestTicksAtArm') for r in rr]),'within1Fraction':qstats([r.get('within1ReceiptFractionBeforeArm') for r in rr]),'depthDecreaseEvents':qstats([r.get('strictPastDepthDecreaseEvents') for r in rr]),'pairSumAtArmAsk':qstats([r.get('pairSumAtArmAsk') for r in rr]),'levelZeroSeenRate':sum(bool(r.get('strictPastLevelZeroSeen')) for r in rr)/len(rr) if rr else None}
 out={'version':'ETH_V27_ACTIVE_TRIGGER_PASSIVE_CARRIER_STATE_AUDIT_V1','researchOnly':True,'coverage':{'activeTriggeredMarkets':len(rows),'completed30s':len(comp),'failed30s':len(fail)},'completed':block(comp),'failed':block(fail),'rows':rows,'boundary':['Strict-past passive carrier/book measurements stop at V27 ACTIVE_ARM.','V23 future completion outcome is evaluation only.','No active threshold selected from this cohort.','No behavior change.']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
