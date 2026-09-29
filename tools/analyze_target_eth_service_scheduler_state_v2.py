from __future__ import annotations
import argparse, collections, json, math, sqlite3, statistics, time
from pathlib import Path

EPS=1e-9
SIDES=('UP','DOWN')
TRANSITIONS=('REPAIR_PLUS_EXPAND','REPAIR_PRESENT_NO_NEW_EXPAND','EXPAND_ONLY_WITH_DEBT')

def opp(s): return 'DOWN' if s=='UP' else 'UP'

def pct(xs,q):
    if not xs:return None
    z=sorted(float(x) for x in xs)
    return z[min(len(z)-1,max(0,int(q*(len(z)-1))))]

def stats(xs):
    z=[float(x) for x in xs if x is not None]
    return {'n':len(z),'mean':sum(z)/len(z) if z else None,'median':statistics.median(z) if z else None,
            'p25':pct(z,.25),'p75':pct(z,.75),'p90':pct(z,.90)}

def age_bin(v):
    if v is None:return 'NA'
    if v<5000:return '<5s'
    if v<10000:return '5-10s'
    if v<20000:return '10-20s'
    if v<40000:return '20-40s'
    if v<60000:return '40-60s'
    return '>=60s'

def qty_bin(v):
    if v is None:return 'NA'
    if v<2:return '<2'
    if v<5:return '2-5'
    if v<10:return '5-10'
    if v<20:return '10-20'
    return '>=20'

def lot_bin(v):
    if v is None:return 'NA'
    if v<=1:return '1'
    if v==2:return '2'
    if v==3:return '3'
    return '4+'

def share_bin(v):
    if v is None:return 'NA'
    if v<.25:return '<0.25'
    if v<.5:return '0.25-0.50'
    if v<.75:return '0.50-0.75'
    return '>=0.75'

def wait_clock_bin(v):
    if v is None:return 'NA'
    if v==0:return '0'
    if v==1:return '1'
    if v==2:return '2'
    return '3+'

def elapsed_bin(v):
    if v is None:return 'NA'
    if v<3000:return '<3s'
    if v<7000:return '3-7s'
    if v<15000:return '7-15s'
    if v<30000:return '15-30s'
    return '>=30s'

def delta_bin(v):
    if v is None:return 'NA'
    if v<-5:return '<-5'
    if v<-1:return '-5..-1'
    if v<=1:return '-1..1'
    if v<=5:return '1..5'
    return '>5'

def summarize_counts(counter):
    total=sum(counter.values())
    rpe=counter.get('REPAIR_PLUS_EXPAND',0); ronly=counter.get('REPAIR_PRESENT_NO_NEW_EXPAND',0); exp=counter.get('EXPAND_ONLY_WITH_DEBT',0)
    repairs=rpe+ronly
    return {'n':total,'counts':{k:int(counter.get(k,0)) for k in TRANSITIONS},
            'serviceNowRate':repairs/total if total else None,
            'expandOnlyRate':exp/total if total else None,
            'repairPlusExpandRate':rpe/total if total else None,
            'repairOnlyRate':ronly/total if total else None,
            'compositeAmongRepair':rpe/repairs if repairs else None}

def feature_table(rows,key):
    by=collections.defaultdict(collections.Counter)
    for r in rows:by[str(r[key])][r['transition']]+=1
    return {k:summarize_counts(v) for k,v in sorted(by.items())}

def mi_bits(rows,key):
    # Discrete mutual information between binned strict-past feature and current transition.
    n=len(rows)
    if not n:return 0.0
    x=collections.Counter(r[key] for r in rows);y=collections.Counter(r['transition'] for r in rows);xy=collections.Counter((r[key],r['transition']) for r in rows)
    out=0.0
    for (a,b),c in xy.items():
        p=c/n;px=x[a]/n;py=y[b]/n
        if p>0 and px>0 and py>0:out+=p*math.log2(p/(px*py))
    return out

def cross_table(rows,k1,k2,min_n=100):
    by=collections.defaultdict(collections.Counter)
    for r in rows:by[(str(r[k1]),str(r[k2]))][r['transition']]+=1
    out={}
    for (a,b),c in sorted(by.items()):
        s=summarize_counts(c)
        if s['n']>=min_n:out[f'{a}|{b}']=s
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default='data/target_wallet_official_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args();started=time.time()
    c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    meta=c.execute("select count(*) events,count(distinct market_id) markets,min(event_ms) min_ms,max(event_ms) max_ms from wallet_shadow_target_events where asset='ETH'").fetchone()
    ev=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"));c.close()
    by=collections.defaultdict(lambda:collections.defaultdict(list))
    for r in ev:by[int(r['market_id'])][int(r['event_ms'])].append(r)
    rows=[];viol=collections.Counter();lot_id=0
    for mid,clocks in sorted(by.items()):
        qs={s:collections.deque() for s in SIDES};outqty={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES}
        prior_no_repair_clocks=0;run_anchor_t=None;last_transition=None;last_post_total=None;last_repair_t=None
        for t,legs in sorted(clocks.items()):
            pre=dict(outqty);pre_total=pre['UP']+pre['DOWN'];pre_lots=len(qs['UP'])+len(qs['DOWN'])
            pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None)
            oldest=qs[pre_side][0] if pre_side else None
            oldest_age=t-int(oldest['bornAt']) if oldest else None;oldest_rem=float(oldest['remaining']) if oldest else None
            strict_prior_wait=prior_no_repair_clocks
            strict_elapsed=(t-run_anchor_t) if (run_anchor_t is not None and pre_total>EPS) else None
            strict_since_repair=(t-last_repair_t) if (last_repair_t is not None and pre_total>EPS) else None
            strict_delta=(pre_total-last_post_total) if (last_post_total is not None and pre_total>EPS) else None
            agg={s:{'q':0.0,'notional':0.0} for s in SIDES}
            for r in legs:
                s=str(r['side']).upper();q=float(r['shares']);p=float(r['price'])
                if s not in SIDES or q<=EPS:continue
                agg[s]['q']+=q;agg[s]['notional']+=q*p
            for s in SIDES:agg[s]['px']=agg[s]['notional']/agg[s]['q'] if agg[s]['q']>EPS else None
            rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES};touched=set()
            for pay in SIDES:
                dq=qs[opp(pay)];need=rem[pay]
                while need>EPS and dq:
                    lot=dq[0];take=min(need,float(lot['remaining']))
                    if take<=EPS:break
                    lot['remaining']-=take;outqty[opp(pay)]-=take;repair[pay]+=take;need-=take;touched.add(int(lot['id']))
                    if lot['remaining']<=EPS:dq.popleft()
                rem[pay]=need
            pair_now=min(rem['UP'],rem['DOWN'])
            if pair_now>EPS:rem['UP']-=pair_now;rem['DOWN']-=pair_now
            birth_qty=0.0
            for s in SIDES:
                q=rem[s]
                if q<=EPS:continue
                lot_id+=1;lot={'id':lot_id,'side':s,'bornAt':t,'remaining':q,'price':float(agg[s]['px'])};qs[s].append(lot);outqty[s]+=q;birth_qty+=q
            repair_total=repair['UP']+repair['DOWN'];post_total=outqty['UP']+outqty['DOWN']
            if pre_total>EPS:
                if repair_total>EPS and birth_qty>EPS:tr='REPAIR_PLUS_EXPAND'
                elif repair_total>EPS:tr='REPAIR_PRESENT_NO_NEW_EXPAND'
                elif birth_qty>EPS:tr='EXPAND_ONLY_WITH_DEBT'
                else:tr='NO_REPAIR_NO_EXPAND_WITH_DEBT'
                if tr!='NO_REPAIR_NO_EXPAND_WITH_DEBT':
                    oldest_share=(oldest_rem/pre_total) if oldest_rem is not None and pre_total>EPS else None
                    rr={'marketId':mid,'t':t,'transition':tr,'oldestAgeMs':oldest_age,'preOutstandingQty':pre_total,'preLotCount':pre_lots,
                        'oldestRemainingQty':oldest_rem,'oldestShare':oldest_share,'priorNoRepairClocks':strict_prior_wait,
                        'elapsedSinceRunAnchorMs':strict_elapsed,'elapsedSinceLastRepairMs':strict_since_repair,'priorTransition':last_transition or 'NONE',
                        'preOutstandingDeltaFromPriorClock':strict_delta,'repairQty':repair_total,'expandBirthQty':birth_qty,'touchedLots':len(touched),'postOutstandingQty':post_total}
                    rr.update({'ageBin':age_bin(oldest_age),'qtyBin':qty_bin(pre_total),'lotBin':lot_bin(pre_lots),'oldestShareBin':share_bin(oldest_share),
                               'priorNoRepairBin':wait_clock_bin(strict_prior_wait),'elapsedRunBin':elapsed_bin(strict_elapsed),
                               'elapsedSinceRepairBin':elapsed_bin(strict_since_repair),'deltaBin':delta_bin(strict_delta)})
                    rows.append(rr)
                if repair_total>EPS:
                    prior_no_repair_clocks=0;run_anchor_t=t if post_total>EPS else None;last_repair_t=t
                else:
                    if prior_no_repair_clocks==0:run_anchor_t=t
                    prior_no_repair_clocks+=1
                last_transition=tr;last_post_total=post_total
            else:
                prior_no_repair_clocks=0;run_anchor_t=None;last_transition=None;last_post_total=post_total
            hold['UP']+=agg['UP']['q'];hold['DOWN']+=agg['DOWN']['q'];gap=abs(hold['UP']-hold['DOWN'])
            if abs(post_total-gap)>1e-6:viol['outstandingGapMismatch']+=1
            if outqty['UP']>EPS and outqty['DOWN']>EPS:viol['twoOutstandingSides']+=1
    rows=[r for r in rows if r['transition'] in TRANSITIONS]
    overall=summarize_counts(collections.Counter(r['transition'] for r in rows))
    features=['ageBin','qtyBin','lotBin','oldestShareBin','priorNoRepairBin','elapsedRunBin','elapsedSinceRepairBin','deltaBin','priorTransition']
    tables={k:feature_table(rows,k) for k in features}
    assoc=sorted(({'feature':k,'mutualInformationBits':mi_bits(rows,k),'levels':len(set(r[k] for r in rows))} for k in features),key=lambda x:x['mutualInformationBits'],reverse=True)
    crosses={
      'age_x_lots':cross_table(rows,'ageBin','lotBin'),
      'age_x_qty':cross_table(rows,'ageBin','qtyBin'),
      'wait_x_lots':cross_table(rows,'priorNoRepairBin','lotBin'),
      'prior_transition_x_wait':cross_table(rows,'priorTransition','priorNoRepairBin'),
      'oldest_share_x_lots':cross_table(rows,'oldestShareBin','lotBin')
    }
    out={'version':'TARGET_ETH_SERVICE_SCHEDULER_STATE_V2','date':'2026-09-06','researchOnly':True,'actionAuthority':False,
         'sourceDb':a.db,'sourceSnapshot':dict(meta),'runtimeSeconds':time.time()-started,'rows':len(rows),'overall':overall,
         'featureAssociation':assoc,'conditionalTables':tables,'crossTables':crosses,'invariantViolations':dict(viol),
         'interpretationBoundary':['all conditioning features are strict-past state at Target fill-event clock before current fills are allocated',
           'current transition is descriptive label only and is never fed back as runtime authority','event clocks are Target fill clocks, not public-book receipt clocks',
           'FIFO is reconstruction hypothesis, not claim of private Target implementation','no winner/PnL/future action used','conditional rates are teacher evidence, not hard runtime thresholds']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'rows':len(rows),'overall':overall,'featureAssociation':assoc,'invariantViolations':dict(viol)},ensure_ascii=False))

if __name__=='__main__':main()
