from __future__ import annotations
import argparse,collections,json,math,sqlite3,statistics,threading,time
from pathlib import Path

EPS=1e-9
SIDES=('UP','DOWN')

def opp(s): return 'DOWN' if s=='UP' else 'UP'

def weighted_quantile(rows,key='pairSum',wkey='qty',q=.5):
    z=sorted((float(r[key]),float(r[wkey])) for r in rows if r.get(key) is not None and float(r.get(wkey) or 0)>EPS)
    total=sum(w for _,w in z)
    if total<=EPS:return None
    target=q*total;acc=0.0
    for x,w in z:
        acc+=w
        if acc+EPS>=target:return x
    return z[-1][0]

def dist(rows,key='pairSum',wkey='qty'):
    if not rows:return {'records':0,'qty':0.0,'weightedMean':None,'weightedMedian':None,'weightedP10':None,'weightedP90':None,'nonDamagingQtyShare':None}
    qty=sum(float(r.get(wkey) or 0) for r in rows)
    mean=(sum(float(r[key])*float(r[wkey]) for r in rows if r.get(key) is not None)/qty) if qty>EPS else None
    nd=sum(float(r[wkey]) for r in rows if r.get(key) is not None and float(r[key])<=1.0+EPS)
    return {'records':len(rows),'qty':qty,'weightedMean':mean,'weightedMedian':weighted_quantile(rows,key,wkey,.5),'weightedP10':weighted_quantile(rows,key,wkey,.1),'weightedP90':weighted_quantile(rows,key,wkey,.9),'nonDamagingQtyShare':nd/qty if qty>EPS else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'TARGET_ETH_RESP_LEDGER_V3','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'TARGET_ETH_RESP_LEDGER_V3_START'}),flush=True)
    try:
        c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
        tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")};
        if 'eth_events' in tables:
            events=list(c.execute("select id,leg_id,market_id,role,side,order_hash,transaction_hash,settlement_id,event_ms,observed_at_ms,price,shares from eth_events order by market_id,event_ms,id"))
        else:
            events=list(c.execute("select id,leg_id,market_id,role,side,order_hash,transaction_hash,settlement_id,event_ms,observed_at_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"))
        markets={int(r['market_id']) for r in events};c.close()
        by=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in events:by[int(r['market_id'])][int(r['event_ms'])].append(r)
        pair_rows=[];direct_rows=[];completed=[];all_lots=[];market_rows=[];viol=collections.Counter();route=collections.Counter();clock_counts=collections.Counter();lot_id=0
        for mi,(mid,clocks) in enumerate(sorted(by.items()),1):
            up=down=0.0;queues={'UP':collections.deque(),'DOWN':collections.deque()};market_pair=[];market_direct=[];market_lots=[];market_composite=0;market_clocks=0
            for t,legs in sorted(clocks.items()):
                market_clocks+=1;clock_counts['clocks']+=1
                agg={s:{'q':0.0,'notional':0.0,'maker':0.0,'taker':0.0,'legs':0} for s in SIDES}
                for r in legs:
                    s=str(r['side']).upper();q=float(r['shares']);px=float(r['price']);rr=str(r['role']).upper();
                    if s not in SIDES or q<=EPS:continue
                    agg[s]['q']+=q;agg[s]['notional']+=q*px;agg[s]['legs']+=1
                    if rr=='MAKER':agg[s]['maker']+=q
                    elif rr=='TAKER':agg[s]['taker']+=q
                for s in SIDES:
                    agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
                input_q={s:agg[s]['q'] for s in SIDES};repair_q={s:0.0 for s in SIDES};expand_q={s:0.0 for s in SIDES};rem=dict(input_q)
                # Existing responsibility is paid first. Newly-created residuals at this clock cannot be repaired at the same clock.
                for pay_side in SIDES:
                    debt_side=opp(pay_side);dq=queues[debt_side];need=rem[pay_side]
                    if need<=EPS or not dq:continue
                    maker_frac=agg[pay_side]['maker']/agg[pay_side]['q'] if agg[pay_side]['q']>EPS else 0.0
                    taker_frac=agg[pay_side]['taker']/agg[pay_side]['q'] if agg[pay_side]['q']>EPS else 0.0
                    last_born=-1
                    while need>EPS and dq:
                        lot=dq[0]
                        if int(lot['bornAt'])<last_born:viol['fifoAgeOrder']+=1
                        last_born=int(lot['bornAt']);take=min(need,float(lot['remaining']))
                        ps=float(lot['price'])+float(agg[pay_side]['px']);rec={'marketId':mid,'t':t,'expandLotId':lot['id'],'expandSide':debt_side,'repairSide':pay_side,'qty':take,'expandPrice':lot['price'],'repairPrice':agg[pay_side]['px'],'pairSum':ps,'responsibilityAgeMs':t-int(lot['bornAt']),'expandBornAt':lot['bornAt'],'expandRouteMakerShare':lot['makerShare'],'repairRouteMakerShare':maker_frac}
                        pair_rows.append(rec);market_pair.append(rec);repair_q[pay_side]+=take;route['repairMakerQty']+=take*maker_frac;route['repairTakerQty']+=take*taker_frac
                        lot['remaining']-=take;lot['paidQty']+=take;lot['paymentClocks'].add(t);lot['lastPaidAt']=t;need-=take
                        if lot['remaining']<=EPS:
                            done=dq.popleft();done['completedAt']=t;done['durationMs']=t-int(done['bornAt']);done['paymentClockCount']=len(done['paymentClocks']);completed.append(done)
                        if take<=EPS:break
                    rem[pay_side]=need
                # Remaining simultaneous two-sided acquisition is a direct pair build, not future lineage.
                pair_now=min(rem['UP'],rem['DOWN'])
                if pair_now>EPS:
                    ps=float(agg['UP']['px'])+float(agg['DOWN']['px']);rec={'marketId':mid,'t':t,'qty':pair_now,'upPrice':agg['UP']['px'],'downPrice':agg['DOWN']['px'],'pairSum':ps,'upLegs':agg['UP']['legs'],'downLegs':agg['DOWN']['legs']};direct_rows.append(rec);market_direct.append(rec);rem['UP']-=pair_now;rem['DOWN']-=pair_now
                # One-sided residual births a new Expand responsibility.
                for s in SIDES:
                    q=rem[s]
                    if q<=EPS:continue
                    lot_id+=1;maker_share=agg[s]['maker']/agg[s]['q'] if agg[s]['q']>EPS else 0.0;taker_share=agg[s]['taker']/agg[s]['q'] if agg[s]['q']>EPS else 0.0
                    lot={'id':lot_id,'marketId':mid,'side':s,'bornAt':t,'initialQty':q,'remaining':q,'paidQty':0.0,'price':float(agg[s]['px']),'makerShare':maker_share,'takerShare':taker_share,'paymentClocks':set(),'completedAt':None};queues[s].append(lot);all_lots.append(lot);market_lots.append(lot);expand_q[s]+=q;route['expandMakerQty']+=q*maker_share;route['expandTakerQty']+=q*taker_share
                if sum(repair_q.values())>EPS and sum(expand_q.values())>EPS:
                    market_composite+=1;clock_counts['repairThenNewExpandCompositeClocks']+=1
                if sum(repair_q.values())>EPS:clock_counts['repairClocks']+=1
                if pair_now>EPS:clock_counts['directPairClocks']+=1
                if sum(expand_q.values())>EPS:clock_counts['expandBirthClocks']+=1
                # Physical holdings and conservation invariants.
                up+=input_q['UP'];down+=input_q['DOWN']
                for s in SIDES:
                    allocated=repair_q[s]+pair_now+expand_q[s]
                    if abs(input_q[s]-allocated)>1e-7:viol['physicalAllocationConservation']+=1
                out_up=sum(max(0.0,float(x['remaining'])) for x in queues['UP']);out_dn=sum(max(0.0,float(x['remaining'])) for x in queues['DOWN']);out_total=out_up+out_dn;gap=abs(up-down)
                if abs(out_total-gap)>1e-6:viol['outstandingGapMismatch']+=1
                if out_up>EPS and out_dn>EPS:viol['twoOutstandingSides']+=1
                for s in SIDES:
                    for lot in queues[s]:
                        if lot['paidQty']>lot['initialQty']+1e-7 or lot['remaining']<-1e-7:viol['lotOverpaid']+=1
            open_qty=sum(float(x['remaining']) for s in SIDES for x in queues[s]);multi=sum(1 for x in market_lots if x.get('completedAt') is not None and len(x['paymentClocks'])>=2);done=sum(1 for x in market_lots if x.get('completedAt') is not None)
            market_rows.append({'marketId':mid,'clocks':market_clocks,'expandResponsibilities':len(market_lots),'completedResponsibilities':done,'multiPaymentCompletedResponsibilities':multi,'repairPairRecords':len(market_pair),'directPairRecords':len(market_direct),'compositeRepairThenExpandClocks':market_composite,'terminalOutstandingQty':open_qty})
            if mi%500==0:print(json.dumps({'progressMarkets':mi,'of':len(by)}),flush=True)
        # Convert sets for serialization only after all diagnostics.
        for lot in all_lots:
            if isinstance(lot.get('paymentClocks'),set):lot['paymentClocks']=sorted(lot['paymentClocks'])
        completed_rows=[x for x in all_lots if x.get('completedAt') is not None]
        durations=[float(x['completedAt'])-float(x['bornAt']) for x in completed_rows];payments=[int(len(x.get('paymentClocks') or [])) for x in completed_rows]
        repair_qty=sum(float(x['qty']) for x in pair_rows);direct_qty=sum(float(x['qty']) for x in direct_rows);birth_qty=sum(float(x['initialQty']) for x in all_lots);paid_total=sum(float(x['paidQty']) for x in all_lots);outstanding=sum(float(x['remaining']) for x in all_lots)
        decision='KEEP_V3_LEDGER_SEMANTICS_FOR_NEXT_SHADOW' if not viol and sum(p>=2 for p in payments)>=100 else ('ACCOUNTING_FAIL' if viol else 'INSUFFICIENT_MULTI_PAYMENT_SUPPORT')
        out={'version':'TARGET_ETH_RESPONSIBILITY_LEDGER_V3_ATOMIC_FIFO','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'sourceDb':str(a.db),'dataset':{'events':len(events),'markets':len(markets),'atomicClocks':clock_counts['clocks']},'allocation':{'expandBirthQty':birth_qty,'repairPaidQty':repair_qty,'directPairQty':direct_qty,'totalResponsibilityPaidQty':paid_total,'terminalOutstandingResponsibilityQty':outstanding,'responsibilitiesBorn':len(all_lots),'responsibilitiesCompleted':len(completed_rows),'multiPaymentCompletedResponsibilities':sum(p>=2 for p in payments),'multiPaymentCompletedShare':sum(p>=2 for p in payments)/len(completed_rows) if completed_rows else None},'clockTopology':dict(clock_counts),'repairSettlementPairEconomics':dist(pair_rows),'directPairEconomics':dist(direct_rows),'responsibilityDurationMs':{'median':statistics.median(durations) if durations else None,'p10':sorted(durations)[max(0,int(.1*len(durations))-1)] if durations else None,'p90':sorted(durations)[min(len(durations)-1,int(.9*len(durations)))] if durations else None},'repairPaymentsPerCompletedResponsibility':{'median':statistics.median(payments) if payments else None,'mean':statistics.mean(payments) if payments else None,'max':max(payments) if payments else None},'routeQty':dict(route),'invariantViolations':dict(viol),'decision':decision,'marketSummary':market_rows,'sampleRepairPairs':pair_rows[:100],'sampleResponsibilities':all_lots[:100],'interpretation':['This reconstruction pairs each Repair payment with an already-existing opposite-side Expand responsibility; it does not pair a crossing Repair with a future Expand as the same responsibility.','Same-clock fill legs are processed as one atomic clock batch using side VWAP, avoiding arbitrary hash ordering.','FIFO is a reconstruction hypothesis, not a claim about Target internal implementation.','Every quantity is single-use: prior Expand responsibility can be paid once; newly-created residual becomes a new responsibility.'],'boundary':['read-only Target fill-leg reconstruction','no winner/PnL/future action feature','no runtime authority','no 8781','no dream fill']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'dataset':out['dataset'],'allocation':out['allocation'],'clockTopology':out['clockTopology'],'repairPair':out['repairSettlementPairEconomics'],'violations':out['invariantViolations']},ensure_ascii=False),flush=True)
    finally:stop.set()

if __name__=='__main__':main()
