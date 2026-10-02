from __future__ import annotations
import argparse,collections,json,sqlite3,statistics,time
from pathlib import Path
EPS=1e-9;SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def pct(xs,q):
 if not xs:return None
 z=sorted(xs);return z[min(len(z)-1,max(0,int(q*(len(z)-1))))]
def stats(xs):
 z=[float(x) for x in xs if x is not None]
 return {'n':len(z),'mean':sum(z)/len(z) if z else None,'median':statistics.median(z) if z else None,'p25':pct(z,.25),'p75':pct(z,.75),'p90':pct(z,.9)}
def block(rows):
 return {'clocks':len(rows),'oldestAgeMs':stats([r['oldestAgeMs'] for r in rows]),'outstandingQty':stats([r['preOutstandingQty'] for r in rows]),'lotCount':stats([r['preLotCount'] for r in rows]),'paymentFraction':stats([r['paymentFraction'] for r in rows if r['paymentFraction'] is not None]),'postOutstandingQty':stats([r['postOutstandingQty'] for r in rows]),'crossLotClocks':sum(r['touchedLots']>=2 for r in rows),'repairQty':sum(r['repairQty'] for r in rows),'expandBirthQty':sum(r['expandBirthQty'] for r in rows)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',default='data/target_wallet_official_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args();started=time.time()
 c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 meta=c.execute("select count(*) events,count(distinct market_id) markets,min(event_ms) min_ms,max(event_ms) max_ms from wallet_shadow_target_events where asset='ETH'").fetchone()
 ev=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"));c.close()
 by=collections.defaultdict(lambda:collections.defaultdict(list))
 for r in ev:by[int(r['market_id'])][int(r['event_ms'])].append(r)
 rows=[];lots=[];viol=collections.Counter();lot_id=0;wait_clocks=[];wait_ms=[];first_payment_ages=[];completion_durations=[];multi_payment=[]
 for mid,clocks in sorted(by.items()):
  qs={s:collections.deque() for s in SIDES};outqty={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES};consecutive_no_repair=0;debt_run_started=None
  for t,legs in sorted(clocks.items()):
   pre=dict(outqty);pre_total=pre['UP']+pre['DOWN'];pre_lots=len(qs['UP'])+len(qs['DOWN'])
   pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None)
   oldest=None
   if pre_side:
    oldest=qs[pre_side][0]
   oldest_age=(t-int(oldest['bornAt'])) if oldest else None;oldest_rem=float(oldest['remaining']) if oldest else None
   agg={s:{'q':0.0,'notional':0.0,'maker':0.0,'taker':0.0} for s in SIDES}
   for r in legs:
    s=str(r['side']).upper();q=float(r['shares']);p=float(r['price']);role=str(r['role']).upper()
    if s not in SIDES or q<=EPS:continue
    agg[s]['q']+=q;agg[s]['notional']+=q*p
    if role=='MAKER':agg[s]['maker']+=q
    elif role=='TAKER':agg[s]['taker']+=q
   for s in SIDES:agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
   rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES};touched=set();maker_repair=0.0;taker_repair=0.0
   for pay in SIDES:
    dq=qs[opp(pay)];need=rem[pay]
    if need<=EPS:continue
    mf=agg[pay]['maker']/agg[pay]['q'] if agg[pay]['q']>EPS else 0.0;tf=agg[pay]['taker']/agg[pay]['q'] if agg[pay]['q']>EPS else 0.0
    while need>EPS and dq:
     lot=dq[0];take=min(need,float(lot['remaining']))
     if take<=EPS:break
     if lot['firstPaidAt'] is None:
      lot['firstPaidAt']=t;first_payment_ages.append(t-int(lot['bornAt']))
     lot['remaining']-=take;outqty[opp(pay)]-=take;lot['paidQty']+=take;lot['paymentClocks'].add(t);lot['lastPaidAt']=t;repair[pay]+=take;need-=take;touched.add(int(lot['id']));maker_repair+=take*mf;taker_repair+=take*tf
     if lot['remaining']<=EPS:
      done=dq.popleft();done['remaining']=0.0;done['completedAt']=t;done['durationMs']=t-int(done['bornAt']);completion_durations.append(done['durationMs']);multi_payment.append(len(done['paymentClocks']))
    rem[pay]=need
   pair_now=min(rem['UP'],rem['DOWN'])
   if pair_now>EPS:rem['UP']-=pair_now;rem['DOWN']-=pair_now
   birth_qty=0.0
   for s in SIDES:
    q=rem[s]
    if q<=EPS:continue
    lot_id+=1;lot={'id':lot_id,'marketId':mid,'side':s,'bornAt':t,'initialQty':q,'remaining':q,'paidQty':0.0,'price':float(agg[s]['px']),'paymentClocks':set(),'firstPaidAt':None,'lastPaidAt':None,'completedAt':None};qs[s].append(lot);outqty[s]+=q;lots.append(lot);birth_qty+=q
   repair_total=repair['UP']+repair['DOWN'];post=dict(outqty);post_total=post['UP']+post['DOWN']
   if pre_total>EPS:
    if repair_total>EPS and birth_qty>EPS:transition='REPAIR_PLUS_EXPAND'
    elif repair_total>EPS:transition='REPAIR_PRESENT_NO_NEW_EXPAND'
    elif birth_qty>EPS:transition='EXPAND_ONLY_WITH_DEBT'
    else:transition='NO_REPAIR_NO_EXPAND_WITH_DEBT'
    if repair_total>EPS:
     wait_clocks.append(consecutive_no_repair)
     if debt_run_started is not None:wait_ms.append(t-debt_run_started)
     consecutive_no_repair=0;debt_run_started=t if post_total>EPS else None
    else:
     if consecutive_no_repair==0:debt_run_started=t
     consecutive_no_repair+=1
    rows.append({'marketId':mid,'t':t,'transition':transition,'preOutstandingSide':pre_side,'preOutstandingQty':pre_total,'preLotCount':pre_lots,'oldestAgeMs':oldest_age,'oldestRemainingQty':oldest_rem,'repairQty':repair_total,'paymentFraction':repair_total/pre_total if repair_total>EPS else None,'touchedLots':len(touched),'repairMakerQty':maker_repair,'repairTakerQty':taker_repair,'expandBirthQty':birth_qty,'directPairQty':pair_now,'postOutstandingQty':post_total,'postLotCount':len(qs['UP'])+len(qs['DOWN'])})
   else:
    consecutive_no_repair=0;debt_run_started=None
   hold['UP']+=agg['UP']['q'];hold['DOWN']+=agg['DOWN']['q'];gap=abs(hold['UP']-hold['DOWN'])
   if abs(post_total-gap)>1e-6:viol['outstandingGapMismatch']+=1
   if post['UP']>EPS and post['DOWN']>EPS:viol['twoOutstandingSides']+=1
 groups={k:block([r for r in rows if r['transition']==k]) for k in ['REPAIR_PLUS_EXPAND','REPAIR_PRESENT_NO_NEW_EXPAND','EXPAND_ONLY_WITH_DEBT','NO_REPAIR_NO_EXPAND_WITH_DEBT']}
 repair_rows=[r for r in rows if r['repairQty']>EPS];cross=[r for r in repair_rows if r['touchedLots']>=2];leave_open=[r for r in repair_rows if r['postOutstandingQty']>EPS]
 total_repair=sum(r['repairQty'] for r in repair_rows);maker=sum(r['repairMakerQty'] for r in repair_rows);taker=sum(r['repairTakerQty'] for r in repair_rows)
 out={'version':'TARGET_ETH_ATOMIC_FIFO_SERVICE_URGENCY_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'sourceDb':a.db,'sourceSnapshot':dict(meta),'runtimeSeconds':time.time()-started,'debtEventClocks':len(rows),'transitionGroups':groups,
  'repairService':{'repairClocks':len(repair_rows),'crossLotRepairClocks':len(cross),'crossLotRepairShare':len(cross)/len(repair_rows) if repair_rows else None,'leavesDebtOpenClocks':len(leave_open),'leavesDebtOpenShare':len(leave_open)/len(repair_rows) if repair_rows else None,'repairQty':total_repair,'makerQtyShare':maker/total_repair if total_repair else None,'takerQtyShare':taker/total_repair if total_repair else None,'crossLot':block(cross),'singleLot':block([r for r in repair_rows if r['touchedLots']==1])},
  'managerClockWaitBeforeRepair':{'priorDebtEventClocksWithoutRepair':stats(wait_clocks),'elapsedMsFromDebtRunStart':stats(wait_ms)},'responsibilityFirstPaymentAgeMs':stats(first_payment_ages),'responsibilityCompletionDurationMs':stats(completion_durations),'paymentsPerCompletedResponsibility':stats(multi_payment),'multiPaymentCompletedShare':sum(x>=2 for x in multi_payment)/len(multi_payment) if multi_payment else None,'invariantViolations':dict(viol),
  'interpretationBoundary':['Target fill-event clocks only; not every public-book receipt','existing exact responsibility paid first FIFO','same-clock residual becomes new Expand responsibility after old debt/direct pair allocation','winner/PnL/future action not used','group statistics are descriptive teacher evidence, not runtime thresholds','FIFO is reconstruction hypothesis, not claim of Target private implementation'],'sampleRows':rows[:300]}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ['sourceSnapshot','debtEventClocks','transitionGroups','repairService','managerClockWaitBeforeRepair','responsibilityFirstPaymentAgeMs','responsibilityCompletionDurationMs','paymentsPerCompletedResponsibility','multiPaymentCompletedShare','invariantViolations']},ensure_ascii=False))
if __name__=='__main__':main()
