from __future__ import annotations
import argparse,sqlite3,json,statistics
from collections import defaultdict
from pathlib import Path
EPS=1e-9

def qtile(xs,p):
 if not xs:return None
 xs=sorted(xs);i=(len(xs)-1)*p;lo=int(i);hi=min(len(xs)-1,lo+1);w=i-lo
 return xs[lo]*(1-w)+xs[hi]*w

def analyze_asset(con,asset):
 rows=con.execute("select market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where asset=? and average_price is not null and shares>0 order by market_id,first_event_ms,parent_id",(asset,)).fetchall()
 by=defaultdict(list)
 for m,role,side,t,p,q,pid in rows:by[int(m)].append({'t':int(t),'role':str(role).upper(),'side':str(side).upper(),'price':float(p),'qty':float(q),'parentId':str(pid)})
 episodes=[];partial_takers=[]
 for mid,xs in by.items():
  U=D=C=debt=0.;ep=None;events=[]
  def close_ep(t,reason):
   nonlocal ep
   if ep is not None:
    ep['endAt']=int(t);ep['endReason']=reason;episodes.append(ep);ep=None
  for x in xs:
   preAbs=abs(U-D);preDebt=debt
   if x['side']=='UP':U+=x['qty']
   else:D+=x['qty']
   C+=x['price']*x['qty'];postAbs=abs(U-D);delta=postAbs-preAbs
   if delta>EPS:
    kind='EXPAND';debt=max(0.,preDebt)+delta
    if ep is None:ep={'marketId':mid,'startAt':x['t'],'events':[],'takerRepairCount':0,'makerRepairCount':0,'repairCount':0,'expandCount':0,'maxDebt':debt,'closed':False}
    ep['expandCount']+=1;ep['maxDebt']=max(ep['maxDebt'],debt)
   elif delta<-EPS:
    kind='REPAIR';pay=min(max(0.,preDebt),-delta);debt=max(0.,preDebt-pay)
    if ep is None and preDebt>EPS:ep={'marketId':mid,'startAt':x['t'],'events':[],'takerRepairCount':0,'makerRepairCount':0,'repairCount':0,'expandCount':0,'maxDebt':preDebt,'closed':False}
    if ep is not None:
     ep['repairCount']+=1
     if x['role']=='TAKER':ep['takerRepairCount']+=1
     elif x['role']=='MAKER':ep['makerRepairCount']+=1
   else:kind='FLAT'
   ev={'t':x['t'],'kind':kind,'role':x['role'],'side':x['side'],'qty':x['qty'],'deltaAbsNet':delta,'preDebt':preDebt,'postDebt':debt,'parentId':x['parentId']}
   events.append(ev)
   if ep is not None and kind in ('EXPAND','REPAIR'):ep['events'].append(ev)
   if kind=='REPAIR' and x['role']=='TAKER' and debt>EPS:
    partial_takers.append({'marketId':mid,'t':x['t'],'preDebt':preDebt,'postDebt':debt,'eventIndex':len(events)-1,'eventsRef':events})
   if ep is not None and debt<=EPS:
    ep['closed']=True;close_ep(x['t'],'DEBT_CLEAR')
  if ep is not None:
   ep['closed']=False;close_ep(xs[-1]['t'] if xs else 0,'MARKET_END_OPEN')
 # Episode-level repeated Taker evidence
 eps_t=[e for e in episodes if e['takerRepairCount']>0]
 multi=[e for e in eps_t if e['takerRepairCount']>=2]
 # Event-level: after a partial Taker, before either a new EXPAND or debt clear, does another Repair arrive and what role is it?
 next_role={'TAKER':0,'MAKER':0,'OTHER':0,'NONE':0};same_segment_repeat_taker=0;maker_before_boundary=0;both_before_boundary=0;boundary_expand=0;boundary_clear=0;open_no_boundary=0;segment_rows=[]
 for s in partial_takers:
  evs=s['eventsRef'];idx=s['eventIndex'];found=[];boundary='MARKET_END'
  for y in evs[idx+1:]:
   if y['kind']=='EXPAND':boundary='NEW_EXPAND';break
   if y['kind']=='REPAIR':
    found.append(y)
    if y['postDebt']<=EPS:boundary='DEBT_CLEAR';break
  if boundary=='NEW_EXPAND':boundary_expand+=1
  elif boundary=='DEBT_CLEAR':boundary_clear+=1
  else:open_no_boundary+=1
  nr='NONE'
  if found:
   r=found[0]['role'];nr=r if r in ('TAKER','MAKER') else 'OTHER'
  next_role[nr]+=1
  has_t=any(y['role']=='TAKER' for y in found);has_m=any(y['role']=='MAKER' for y in found)
  same_segment_repeat_taker+=int(has_t);maker_before_boundary+=int(has_m);both_before_boundary+=int(has_t and has_m)
  segment_rows.append({'marketId':s['marketId'],'t':s['t'],'preDebt':s['preDebt'],'postDebt':s['postDebt'],'nextRepairRole':nr,'anotherTakerBeforeExpandOrClear':has_t,'makerRepairBeforeExpandOrClear':has_m,'boundary':boundary,'laterRepairCount':len(found)})
 npt=len(partial_takers)
 return {
  'asset':asset,'markets':len(by),'parentRows':len(rows),'episodes':len(episodes),'episodesWithTakerRepair':len(eps_t),'episodesWithAtLeast2TakerRepair':len(multi),'multiTakerEpisodeRate':len(multi)/len(eps_t) if eps_t else None,
  'takerRepairCountPerTakerEpisode':{'median':qtile([e['takerRepairCount'] for e in eps_t],.5),'p75':qtile([e['takerRepairCount'] for e in eps_t],.75),'p90':qtile([e['takerRepairCount'] for e in eps_t],.9),'max':max([e['takerRepairCount'] for e in eps_t],default=0)},
  'partialTakerRepairEvents':npt,'partialTakerWithAnotherTakerBeforeNextExpandOrDebtClear':same_segment_repeat_taker,'sameSegmentRepeatTakerRate':same_segment_repeat_taker/npt if npt else None,'partialTakerWithMakerRepairBeforeNextExpandOrDebtClear':maker_before_boundary,'makerContinuationRate':maker_before_boundary/npt if npt else None,'partialTakerWithBothMakerAndTakerBeforeBoundary':both_before_boundary,'nextRepairRoleAfterPartialTaker':next_role,'boundaryAfterPartialTaker':{'NEW_EXPAND':boundary_expand,'DEBT_CLEAR':boundary_clear,'MARKET_END':open_no_boundary},
  'examplesMultiTakerEpisodes':[{'marketId':e['marketId'],'takerRepairCount':e['takerRepairCount'],'makerRepairCount':e['makerRepairCount'],'expandCount':e['expandCount'],'maxDebt':e['maxDebt'],'closed':e['closed'],'events':e['events'][:30]} for e in multi[:20]],
  'examplesSameSegmentRepeat':[x for x in segment_rows if x['anotherTakerBeforeExpandOrClear']][:30]
 }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();con=sqlite3.connect(a.db)
 try:res={x:analyze_asset(con,x) for x in ('ETH','BTC')}
 finally:con.close()
 out={'version':'TARGET_BTC_ETH_REPEATED_TAKER_WITHIN_DEBT_EPISODE_V52','researchOnly':True,'source':a.db,'method':'strict chronological actual Target parent fills; debt=running absNet expansion debt; REPAIR reduces debt; event-level same-segment repeat requires another Taker Repair before either next EXPAND or debt clear','caveat':'Debt episode is a structural proxy, not hidden Target responsibility ID. Use rates as architecture evidence only; do not transfer numeric thresholds to OUR.','assets':res};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':{k:{z:v[z] for z in ['markets','episodesWithTakerRepair','episodesWithAtLeast2TakerRepair','multiTakerEpisodeRate','partialTakerRepairEvents','sameSegmentRepeatTakerRate','makerContinuationRate','nextRepairRoleAfterPartialTaker']} for k,v in res.items()}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
