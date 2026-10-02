from __future__ import annotations
import argparse,bisect,json,math,statistics,importlib.util
from pathlib import Path
from collections import defaultdict

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('bridge',HERE/'bridge_eth_v23_repair_taker_hazard_v1.py');bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
bookbase=bridge.bookbase; core=bridge.core; EPS=1e-9
RAW_DEFAULT=bridge.RAW_DEFAULT

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def find_parent_id(raw,first_fill_at,repair_side):
 subs=[s for s in raw['V23']['causal'].get('submitTrace',[]) if s.get('lane')=='REPAIR' and str(s.get('side'))==repair_side and int(s.get('t'))>=int(first_fill_at)]
 if not subs:return None
 return min(subs,key=lambda s:int(s['t'])).get('parentId')

def current_floor(inv):
 up=float(inv.maker_up+inv.taker_up);down=float(inv.maker_down+inv.taker_down);return min(float(inv.cash)+up,float(inv.cash)+down),up,down,float(inv.cash)

def after_buy(floor_state,side,q,px):
 _,up,down,cash=floor_state;cash2=cash-float(q)*float(px)
 if side=='UP':up+=q
 else:down+=q
 return min(cash2+up,cash2+down)

def state_at_received(c,mid,t):
 st=bookbase.load_states(c,mid,int(t)-3000,int(t)+1,'received_at_ms')
 if not st:return None
 ts=[x[0] for x in st];j=bisect.bisect_right(ts,int(t))-1
 return st[j] if j>=0 else None

def block(rr):
 return {'n':len(rr),'markets':len({r['marketId'] for r in rr}),'nonzeroExecutableRate':sum(r['candidateQty']>EPS for r in rr)/len(rr) if rr else None,'cheapCompletionRate':sum(r['classification']=='CHEAP_COMPLETION' for r in rr)/len(rr) if rr else None,'riskOnlyRate':sum(r['classification']=='RISK_ONLY_REPAIR' for r in rr)/len(rr) if rr else None,'fullResidualAtBestRate':sum(r['executableResidualFraction']>=1-EPS for r in rr)/len(rr) if rr else None,'executableResidualFraction':stats([r['executableResidualFraction'] for r in rr]),'shadowFloorDelta':stats([r['shadowFloorDelta'] for r in rr]),'pairSumAtAsk':stats([r['pairSumAtAsk'] for r in rr]),'askPremiumToCeilingTicks':stats([r['askPremiumToCeilingTicks'] for r in rr]),'candidateNotional':stats([r['candidateNotional'] for r in rr]),'warningLeadToEndMs':stats([r['warningLeadToEndMs'] for r in rr])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bridge',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_REPAIR_TAKER_HAZARD_BRIDGE_V1.json');ap.add_argument('--offline',default='data/research/r4_v0/p0_provenance_v1/ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--raw',nargs='*',default=RAW_DEFAULT);ap.add_argument('--output',required=True);a=ap.parse_args()
 br=json.load(open(a.bridge,encoding='utf-8'));off=json.load(open(a.offline,encoding='utf-8'));raw,_=bridge.load_raw(a.raw);offmap={(int(r['marketId']),int(r['cycleIndex'])):r for r in off['rows']};warn=[r for r in br['rows'] if r.get('warningAt') is not None];rows=[]
 import sqlite3;c=sqlite3.connect(f'file:{Path(a.book_db).resolve().as_posix()}?mode=ro',uri=True)
 try:
  for r in warn:
   mid=int(r['marketId']);ci=int(r['cycleIndex']);cy=offmap[(mid,ci)];rr=raw[mid];t=int(r['warningAt']);first_t=int(r['firstFillAt']);first_side=str(cy['firstSide']);repair_side='DOWN' if first_side=='UP' else 'UP';events=bridge.annotate_fills(rr['V23'])
   ff=[e for e in events if int(e['event_ms'])==first_t and str(e['side'])==first_side]
   if not ff:continue
   first=min(ff,key=lambda e:abs(float(e['price'])-float(cy['firstPrice'])) if cy.get('firstPrice') is not None else 0);first_qty=float(first['shares']);pid=find_parent_id(rr,first_t,repair_side)
   repaired=0.0
   for e in events:
    if int(e['event_ms'])<=first_t or int(e['event_ms'])>t:continue
    if str(e['side'])!=repair_side:continue
    if pid is not None and e.get('parentId')==pid:repaired+=float(e['shares'])
   remaining=max(0.0,first_qty-repaired);inv=bridge.make_inventory(events,t);fl=current_floor(inv);st=state_at_received(c,mid,t)
   if st is None:continue
   _,bids,asks=st;dom='UP' if fl[1]>fl[2]+EPS else 'DOWN' if fl[2]>fl[1]+EPS else None;ob=core.outcome_book({'bids':bids,'asks':asks},dom)
   if ob is None:continue
   if repair_side=='UP':ask=float(ob['up_ask']);ask_depth=float(ob['up_ask_depth'])
   else:ask=float(ob['down_ask']);ask_depth=float(ob['down_ask_depth'])
   q=max(0.0,min(remaining,ask_depth));floor_after=after_buy(fl,repair_side,q,ask);floor_delta=floor_after-fl[0];ceiling=float(cy['economicCeiling']);pair=float(cy['firstPrice'])+ask;classification='CHEAP_COMPLETION' if ask<=ceiling+EPS else 'RISK_ONLY_REPAIR';premium=(ask-ceiling)/.01
   passive_pair=None;passive_gain_per_share=None;shadow_gain_per_share=(1.0-ask) if q>EPS else None
   if cy.get('packageRepairFillPrice') is not None:
    passive_pair=float(cy['firstPrice'])+float(cy['packageRepairFillPrice']);passive_gain_per_share=1.0-passive_pair
   rows.append({'marketId':mid,'cycleIndex':ci,'completedWithin30s':bool(r['completedWithin30s']),'warningAt':t,'warningLeadToEndMs':r.get('warningLeadToEndMs'),'teacherMaxHazard':r.get('maxHazard'),'teacherMedianHazard':r.get('medianHazard'),'firstSide':first_side,'repairSide':repair_side,'firstPrice':float(cy['firstPrice']),'firstQty':first_qty,'repairParentId':pid,'repairedQtyBeforeWarning':repaired,'remainingResponsibility':remaining,'repairAsk':ask,'repairAskDepth':ask_depth,'candidateQty':q,'executableResidualFraction':q/remaining if remaining>EPS else 0.0,'economicCeiling':ceiling,'pairSumAtAsk':pair,'classification':classification,'askPremiumToCeilingTicks':premium,'floorBefore':fl[0],'shadowFloorAfter':floor_after,'shadowFloorDelta':floor_delta,'candidateNotional':q*ask,'marginalFloorGainPerRepairShare':shadow_gain_per_share,'shadowPairGainPerShare':1.0-pair,'nonLossPair':pair<=1.0+EPS,'floorAfterNonnegative':floor_after>=-EPS,'realizedPassivePairSum':passive_pair,'realizedPassivePairGainPerShare':passive_gain_per_share,'passiveMinusShadowPairGainPerShare':None if passive_pair is None else pair-passive_pair,'queueEvidence':r.get('queueEvidence')})
 finally:c.close()
 comp=[r for r in rows if r['completedWithin30s']];fail=[r for r in rows if not r['completedWithin30s']]
 out={'version':'ETH_V26_ACTIVE_REPAIR_SHADOW_ECONOMICS','researchOnly':True,'coverage':{'bridgeWarningCycles':len(warn),'resolvedShadowCycles':len(rows),'completedWarningCycles':len(comp),'failedWarningCycles':len(fail)},'summary':{'all':block(rows),'completed':block(comp),'failed':block(fail),'completedPassiveOpportunityCostPerShare':stats([r['passiveMinusShadowPairGainPerShare'] for r in comp]),'nonLossPairRate':sum(r['nonLossPair'] for r in rows)/len(rows) if rows else None,'floorAfterNonnegativeRate':sum(r['floorAfterNonnegative'] for r in rows)/len(rows) if rows else None,'failedNonLossPairRate':sum(r['nonLossPair'] for r in fail)/len(fail) if fail else None,'failedFloorAfterNonnegativeRate':sum(r['floorAfterNonnegative'] for r in fail)/len(fail) if fail else None,'failedFloorImprovementRate':sum(r['shadowFloorDelta']>EPS for r in fail)/len(fail) if fail else None},'rows':rows,'boundary':['No Taker submitted; contemporaneous best ask only.','Candidate quantity is min(owned remaining deterministic Repair responsibility, best-ask depth).','No Target size fraction, BTC threshold, or depth walk.','Future passive completion is post-hoc opportunity-cost evidence only.','ADD_EFFECT is out of scope.']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverage':out['coverage'],'summary':out['summary'],'rows':rows},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
