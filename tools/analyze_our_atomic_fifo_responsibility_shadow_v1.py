from __future__ import annotations
import argparse,collections,json,statistics
from pathlib import Path
EPS=1e-9; SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 src=json.loads(Path(a.input).read_text(encoding='utf-8')); rows=[r for r in src['rows'] if r.get('cell')=='B_RECURSIVE_REPAIR_LADDER_V1']
 all_lots=[];market_rows=[];viol=collections.Counter();clock=collections.Counter();lot_id=0
 for r in rows:
  mid=int(r['marketId']);by=collections.defaultdict(list)
  for f in r.get('confirmedFillAccounting',[]):by[int(f['t'])].append(f)
  queues={s:collections.deque() for s in SIDES};hold={s:0.0 for s in SIDES};market_lots=[]
  for t,legs in sorted(by.items()):
   clock['clocks']+=1
   agg={s:{'q':0.0,'notional':0.0,'roles':collections.Counter()} for s in SIDES}
   for x in legs:
    s=str(x['side']).upper();q=float(x['confirmedQty']);p=float(x['executionPriceFromInheritedSubstrate']);role=str(x.get('role') or 'UNKNOWN')
    if s not in SIDES or q<=EPS:continue
    agg[s]['q']+=q;agg[s]['notional']+=q*p;agg[s]['roles'][role]+=q
   for s in SIDES:agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
   inp={s:agg[s]['q'] for s in SIDES};rem=dict(inp);repair={s:0.0 for s in SIDES};birth={s:0.0 for s in SIDES}
   # Existing responsibility first; newly-created residual cannot be repaired at the same clock.
   for pay in SIDES:
    debt_side=opp(pay);dq=queues[debt_side];need=rem[pay]
    while need>EPS and dq:
     lot=dq[0];take=min(need,float(lot['remaining'])); lot['remaining']-=take;lot['paidQty']+=take;lot['paymentClocks'].add(t);lot['lastPaidAt']=t;repair[pay]+=take;need-=take
     if lot['remaining']<=EPS:
      done=dq.popleft();done['completedAt']=t;done['durationMs']=t-int(done['bornAt']);done['paymentClockCount']=len(done['paymentClocks'])
     if take<=EPS:break
    rem[pay]=need
   pair_now=min(rem['UP'],rem['DOWN'])
   if pair_now>EPS: rem['UP']-=pair_now;rem['DOWN']-=pair_now;clock['directPairClocks']+=1
   for s in SIDES:
    q=rem[s]
    if q<=EPS:continue
    lot_id+=1
    role_total=sum(agg[s]['roles'].values()); role_mix={k:v/role_total for k,v in agg[s]['roles'].items()} if role_total>EPS else {}
    lot={'id':lot_id,'marketId':mid,'side':s,'repairSide':opp(s),'bornAt':t,'initialQty':q,'remaining':q,'paidQty':0.0,'price':agg[s]['px'],'roleMix':role_mix,'paymentClocks':set(),'completedAt':None}
    queues[s].append(lot);all_lots.append(lot);market_lots.append(lot);birth[s]+=q
   if sum(repair.values())>EPS:clock['repairClocks']+=1
   if sum(birth.values())>EPS:clock['expandBirthClocks']+=1
   if sum(repair.values())>EPS and sum(birth.values())>EPS:clock['repairThenNewExpandCompositeClocks']+=1
   for s in SIDES:hold[s]+=inp[s]
   out={s:sum(float(x['remaining']) for x in queues[s]) for s in SIDES}
   if abs((out['UP']+out['DOWN'])-abs(hold['UP']-hold['DOWN']))>1e-6:viol['outstandingGapMismatch']+=1
   if out['UP']>EPS and out['DOWN']>EPS:viol['twoOutstandingSides']+=1
  openq=sum(float(x['remaining']) for s in SIDES for x in queues[s]);done=[x for x in market_lots if x.get('completedAt') is not None]
  market_rows.append({'marketId':mid,'fills':len(r.get('confirmedFillAccounting',[])),'responsibilitiesBorn':len(market_lots),'completed':len(done),'multiPaymentCompleted':sum(len(x['paymentClocks'])>=2 for x in done),'terminalOutstandingQty':openq,'pnl':r.get('pnlDiagnosticOnly'),'floor':r.get('floor')})
 for lot in all_lots:
  if isinstance(lot['paymentClocks'],set):lot['paymentClocks']=sorted(lot['paymentClocks'])
 done=[x for x in all_lots if x.get('completedAt') is not None];payments=[len(x['paymentClocks']) for x in done];dur=[x['durationMs'] for x in done]
 out={'version':'OUR_ATOMIC_FIFO_RESPONSIBILITY_SHADOW_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'input':a.input,'markets':len(rows),
  'allocation':{'responsibilitiesBorn':len(all_lots),'responsibilitiesCompleted':len(done),'multiPaymentCompleted':sum(x>=2 for x in payments),'multiPaymentCompletedShare':sum(x>=2 for x in payments)/len(done) if done else None,'terminalOutstandingQty':sum(float(x['remaining']) for x in all_lots)},
  'responsibilityDurationMs':{'median':statistics.median(dur) if dur else None,'p10':sorted(dur)[max(0,int(.1*len(dur))-1)] if dur else None,'p90':sorted(dur)[min(len(dur)-1,int(.9*len(dur)))] if dur else None},
  'paymentsPerCompletedResponsibility':{'median':statistics.median(payments) if payments else None,'mean':statistics.mean(payments) if payments else None,'max':max(payments) if payments else None},
  'clockTopology':dict(clock),'invariantViolations':dict(viol),'marketSummary':market_rows,'sampleResponsibilities':all_lots[:200],
  'boundary':['behavior-inert posthoc reconstruction from confirmed fills only','existing responsibility paid first','same-clock opposite residual direct-paired before new responsibility birth','new residual cannot repair itself at same clock','no action/PnL/winner authority','no HFT rerun','no 8781']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:out[k] for k in ['markets','allocation','responsibilityDurationMs','paymentsPerCompletedResponsibility','clockTopology','invariantViolations']},ensure_ascii=False))
if __name__=='__main__':main()
