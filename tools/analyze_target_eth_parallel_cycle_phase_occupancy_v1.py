from __future__ import annotations
import argparse, collections, json, math, sqlite3, statistics, threading, time, os
from pathlib import Path

EPS=1e-9
SIDES=('UP','DOWN')
BINS=[i/10 for i in range(11)]

def opp(s): return 'DOWN' if s=='UP' else 'UP'
def qtile(xs,q):
    if not xs:return None
    z=sorted(float(x) for x in xs); i=(len(z)-1)*q; lo=int(math.floor(i)); hi=int(math.ceil(i))
    return z[lo] if lo==hi else z[lo]*(hi-i)+z[hi]*(i-lo)
def phase_bin(p): return min(9,max(0,int(min(max(float(p),0.0),0.999999)*10)))
def phase_of(t): return (int(t)%300000)/300000.0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',default='AUTO'); a=ap.parse_args()
    op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.') if a.output=='AUTO' else Path(a.output).parent)
    outpath=(op/'result.json') if a.output=='AUTO' else Path(a.output); outpath.parent.mkdir(parents=True,exist_ok=True)
    stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'TARGET_PARALLEL_PHASE','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'TARGET_PARALLEL_PHASE_START','db':a.db}),flush=True)
    try:
        c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
        tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        if 'eth_events' in tables:
            sql="select id,market_id,role,side,event_ms,price,shares from eth_events order by market_id,event_ms,id"
        elif 'wallet_shadow_target_events' in tables:
            sql="select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"
        else: raise RuntimeError(f'no supported event table; tables={sorted(tables)}')
        events=list(c.execute(sql)); c.close()
        by=collections.defaultdict(lambda:collections.defaultdict(list))
        for r in events: by[int(r['market_id'])][int(r['event_ms'])].append(r)
        bins=[{'liveBefore':[],'liveAfter':[],'births':0,'completions':0,'repairQty':0.0,'expandQty':0.0,'directPairQty':0.0,
               'repairMakerQty':0.0,'repairTakerQty':0.0,'expandMakerQty':0.0,'expandTakerQty':0.0,'clocks':0,
               'clocksGE2':0,'clocksGE3':0,'clocksGE4':0,'terminalBoundarySamples':[]} for _ in range(10)]
        market_rows=[]; violations=collections.Counter(); lot_id=0
        state_clock_rows=[]
        for mi,(mid,clocks) in enumerate(sorted(by.items()),1):
            queues={'UP':collections.deque(),'DOWN':collections.deque()}; up=down=0.0
            mb=[{'births':0,'completions':0,'repairQty':0.0,'expandQty':0.0,'maxLive':0} for _ in range(10)]
            for t,legs in sorted(clocks.items()):
                b=phase_bin(phase_of(t)); z=bins[b]; z['clocks']+=1
                live_before=sum(1 for s in SIDES for lot in queues[s] if float(lot['remaining'])>EPS)
                z['liveBefore'].append(live_before)
                agg={s:{'q':0.0,'notional':0.0,'maker':0.0,'taker':0.0} for s in SIDES}
                for r in legs:
                    s=str(r['side']).upper(); q=float(r['shares']); px=float(r['price']); rr=str(r['role']).upper()
                    if s not in SIDES or q<=EPS: continue
                    agg[s]['q']+=q; agg[s]['notional']+=q*px
                    if rr=='MAKER': agg[s]['maker']+=q
                    elif rr=='TAKER': agg[s]['taker']+=q
                for s in SIDES: agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
                inp={s:agg[s]['q'] for s in SIDES}; rem=dict(inp); repair={s:0.0 for s in SIDES}; expand={s:0.0 for s in SIDES}; completions=0
                # V3 semantics: existing debt FIFO paid first.
                for pay in SIDES:
                    debt=opp(pay); dq=queues[debt]; need=rem[pay]
                    mf=agg[pay]['maker']/agg[pay]['q'] if agg[pay]['q']>EPS else 0.0
                    tf=agg[pay]['taker']/agg[pay]['q'] if agg[pay]['q']>EPS else 0.0
                    while need>EPS and dq:
                        lot=dq[0]; take=min(need,float(lot['remaining']))
                        repair[pay]+=take; z['repairMakerQty']+=take*mf; z['repairTakerQty']+=take*tf
                        lot['remaining']-=take; lot['paid']+=take; need-=take
                        if lot['remaining']<=EPS: dq.popleft(); completions+=1
                        if take<=EPS: break
                    rem[pay]=need
                pair=min(rem['UP'],rem['DOWN'])
                if pair>EPS: rem['UP']-=pair; rem['DOWN']-=pair
                births=0
                for s in SIDES:
                    q=rem[s]
                    if q<=EPS: continue
                    lot_id+=1; mf=agg[s]['maker']/agg[s]['q'] if agg[s]['q']>EPS else 0.0; tf=agg[s]['taker']/agg[s]['q'] if agg[s]['q']>EPS else 0.0
                    queues[s].append({'id':lot_id,'side':s,'bornAt':t,'initial':q,'remaining':q,'paid':0.0}); expand[s]+=q; births+=1
                    z['expandMakerQty']+=q*mf; z['expandTakerQty']+=q*tf
                up+=inp['UP']; down+=inp['DOWN']
                for s in SIDES:
                    if abs(inp[s]-(repair[s]+pair+expand[s]))>1e-7: violations['physicalAllocationConservation']+=1
                outqty=sum(float(l['remaining']) for s in SIDES for l in queues[s]); gap=abs(up-down)
                if abs(outqty-gap)>1e-6: violations['outstandingGapMismatch']+=1
                if queues['UP'] and queues['DOWN']: violations['twoOutstandingSides']+=1
                live_after=sum(1 for s in SIDES for lot in queues[s] if float(lot['remaining'])>EPS)
                z['liveAfter'].append(live_after); z['births']+=births; z['completions']+=completions
                rq=sum(repair.values()); eq=sum(expand.values()); z['repairQty']+=rq; z['expandQty']+=eq; z['directPairQty']+=pair
                z['clocksGE2']+=int(live_after>=2); z['clocksGE3']+=int(live_after>=3); z['clocksGE4']+=int(live_after>=4)
                mb[b]['births']+=births; mb[b]['completions']+=completions; mb[b]['repairQty']+=rq; mb[b]['expandQty']+=eq; mb[b]['maxLive']=max(mb[b]['maxLive'],live_after)
                if len(state_clock_rows)<200: state_clock_rows.append({'marketId':mid,'t':t,'phase':phase_of(t),'bin':b,'liveBefore':live_before,'births':births,'completions':completions,'liveAfter':live_after,'repairQty':rq,'expandQty':eq})
            terminal_count=sum(1 for s in SIDES for l in queues[s] if l['remaining']>EPS); terminal_qty=sum(float(l['remaining']) for s in SIDES for l in queues[s])
            market_rows.append({'marketId':mid,'maxLiveResponsibilities':max((x['maxLive'] for x in mb),default=0),'terminalLiveResponsibilities':terminal_count,'terminalOutstandingQty':terminal_qty,'phase':mb})
            if mi%100==0: print(json.dumps({'progressMarkets':mi,'of':len(by)}),flush=True)
        phase_rows=[]
        for i,z in enumerate(bins):
            live=z['liveAfter']; totalroute=z['repairMakerQty']+z['repairTakerQty']; exroute=z['expandMakerQty']+z['expandTakerQty']; clocks=max(1,z['clocks'])
            phase_rows.append({'bin':i,'phaseStart':i/10,'phaseEnd':(i+1)/10,'clocks':z['clocks'],'births':z['births'],'birthsPer100Clocks':100*z['births']/clocks,
                'completions':z['completions'],'completionsPer100Clocks':100*z['completions']/clocks,'repairQty':z['repairQty'],'expandBirthQty':z['expandQty'],'directPairQty':z['directPairQty'],
                'repairShareOfRepairPlusExpandQty':z['repairQty']/(z['repairQty']+z['expandQty']) if z['repairQty']+z['expandQty']>EPS else None,
                'repairTakerShare':z['repairTakerQty']/totalroute if totalroute>EPS else None,'expandTakerShare':z['expandTakerQty']/exroute if exroute>EPS else None,
                'liveResponsibilities':{'mean':statistics.mean(live) if live else None,'median':statistics.median(live) if live else None,'p75':qtile(live,.75),'p90':qtile(live,.9),'max':max(live) if live else None},
                'clockShareGE2':z['clocksGE2']/z['clocks'] if z['clocks'] else None,'clockShareGE3':z['clocksGE3']/z['clocks'] if z['clocks'] else None,'clockShareGE4':z['clocksGE4']/z['clocks'] if z['clocks'] else None})
        maxlive=[r['maxLiveResponsibilities'] for r in market_rows]
        early=sum(r['births'] for r in phase_rows[:3])/max(1,sum(r['clocks'] for r in phase_rows[:3]))
        mid=sum(r['births'] for r in phase_rows[3:7])/max(1,sum(r['clocks'] for r in phase_rows[3:7]))
        late=sum(r['births'] for r in phase_rows[7:])/max(1,sum(r['clocks'] for r in phase_rows[7:]))
        repair_early=sum(r['repairQty'] for r in phase_rows[:3]); expand_early=sum(r['expandBirthQty'] for r in phase_rows[:3]); repair_late=sum(r['repairQty'] for r in phase_rows[7:]); expand_late=sum(r['expandBirthQty'] for r in phase_rows[7:])
        summary={'events':len(events),'markets':len(by),'responsibilitiesBorn':lot_id,'marketMaxLiveMedian':statistics.median(maxlive) if maxlive else None,'marketMaxLiveP75':qtile(maxlive,.75),'marketMaxLiveP90':qtile(maxlive,.9),'marketMaxLiveMax':max(maxlive) if maxlive else None,
                 'marketsEverGE2':sum(x>=2 for x in maxlive),'marketsEverGE3':sum(x>=3 for x in maxlive),'marketsEverGE4':sum(x>=4 for x in maxlive),'birthClockRateEarly0_30':early,'birthClockRateMid30_70':mid,'birthClockRateLate70_100':late,
                 'earlyRepairShare':repair_early/(repair_early+expand_early) if repair_early+expand_early>EPS else None,'lateRepairShare':repair_late/(repair_late+expand_late) if repair_late+expand_late>EPS else None}
        decision='SUPPORT_PHASE_ADAPTIVE_PARALLEL_CYCLE_CAPACITY' if not violations and summary['marketsEverGE2']>0 and early>late else ('ACCOUNTING_FAIL' if violations else 'PARALLEL_OR_PHASE_TAPER_NOT_SUPPORTED')
        out={'version':'TARGET_ETH_PARALLEL_CYCLE_PHASE_OCCUPANCY_V1','date':'2026-09-05','researchOnly':True,'actionAuthority':False,'sourceDb':a.db,'decision':decision,'summary':summary,'phaseRows':phase_rows,'invariantViolations':dict(violations),'marketRows':market_rows,'sampleClockRows':state_clock_rows,
             'interpretation':['Live responsibility count means unresolved FIFO Expand lots, not physical order count.','New-cycle/birth density and Repair density are intentionally separated.','Any runtime capacity schedule requires separate OUR microworld/HFT validation; Target numeric phase bins are teacher evidence only.'],
             'boundary':['Target actual fills only','V3 FIFO reconstruction semantics','normalized global 5M phase','no winner/PnL/future action runtime feature','no Target runtime authority','no dream fill','no 8781']}
        outpath.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'summary':summary,'violations':dict(violations),'phaseRows':phase_rows},ensure_ascii=False),flush=True)
    finally: stop.set()
if __name__=='__main__': main()
