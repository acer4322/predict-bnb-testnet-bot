from __future__ import annotations
import gzip,json,math,statistics
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research'
AGG=R/'BTC5M_V49_GENERALIZATION_C30_20260914_RESULT.json'
OUT=R/'BTC5M_V49_REPAIR_PRESSURE_ADD_SHADOW_V1_20260914.json'
V3={2021315,2021302,2020663,2019427,2019008,2018991,2018988,2018854,2018847,2018839,2018666,2018407,2018404}

def job(mid): return f"v49-c30-{mid}-20260914-"+('v3-serial' if mid in V3 else 'v2')
def clamp(x): return max(0.0,min(1.0,float(x)))
def mean(xs): return sum(xs)/len(xs) if xs else None
def med(xs): return statistics.median(xs) if xs else None

def rank(xs):
    order=sorted(range(len(xs)),key=lambda i:xs[i]); out=[0.0]*len(xs); i=0
    while i<len(order):
        j=i+1
        while j<len(order) and xs[order[j]]==xs[order[i]]: j+=1
        r=(i+j-1)/2+1
        for k in range(i,j): out[order[k]]=r
        i=j
    return out

def spearman(xs,ys):
    if len(xs)<3:return None
    rx,ry=rank(xs),rank(ys); mx,my=mean(rx),mean(ry)
    a=sum((x-mx)*(y-my) for x,y in zip(rx,ry)); b=sum((x-mx)**2 for x in rx); c=sum((y-my)**2 for y in ry)
    return a/math.sqrt(b*c) if b>0 and c>0 else None

def main():
    agg=json.loads(AGG.read_text(encoding='utf-8')); bymid={r['market_id']:r for r in agg['rows']}
    events=[]; markets=[]
    for mid,row in bymid.items():
        base=R/'lan_worker_returns'/job(mid)
        res=json.loads((base/'result.json').read_text(encoding='utf-8'))
        tr=json.loads(gzip.decompress((base/'clock_trace.json.gz').read_bytes()))
        births=tr.get('birth_provenance') or {}
        aes=res.get('atomic_responsibility_events') or []
        last_add_index={'UP':None,'DOWN':None}; paid_between={'UP':0.0,'DOWN':0.0}; born_between={'UP':0.0,'DOWN':0.0}
        market_events=[]
        for ei,e in enumerate(aes):
            for p in e.get('payments') or []: paid_between[p['responsibility_side']]+=float(p['qty'])
            for b in e.get('births') or []: born_between[b['side']]+=float(b['qty'])
            add_by_side=defaultdict(float); add_keys=defaultdict(list)
            for fr in e.get('fill_rows') or []:
                meta=births.get(fr.get('key')) or {}
                if meta.get('purpose')=='ADD':
                    s=fr['side']; add_by_side[s]+=float(fr['fill_increment']); add_keys[s].append(fr['key'])
            if not add_by_side: continue
            mst=e.get('manager_state') or {}; inv_after={k:float(v) for k,v in (mst.get('inv') or {}).items()}
            inv_before={'UP':inv_after.get('UP',0.0)-float(e.get('fill_up') or 0.0),'DOWN':inv_after.get('DOWN',0.0)-float(e.get('fill_down') or 0.0)}
            for s,q in add_by_side.items():
                o='DOWN' if s=='UP' else 'UP'; debt=float((e.get('outstanding_before') or {}).get(s,0.0))
                side_inv=max(0.0,inv_before[s]); opp_inv=max(0.0,inv_before[o]); gap=max(0.0,side_inv-opp_inv)
                pressure_inventory=debt/max(side_inv,1e-9) if side_inv>1e-9 else (1.0 if debt>0 else 0.0)
                pressure_gap=debt/max(gap,1e-9) if gap>1e-9 else (1.0 if debt>0 else 0.0)
                coverage_weight=clamp(1.0-pressure_inventory)
                prev_paid=paid_between[s]; prev_born=born_between[s]
                service_ratio=prev_paid/max(prev_born,1e-9) if prev_born>1e-9 else (1.0 if debt<=1e-9 else 0.0)
                ev=dict(market_id=mid,event_index=ei,t=int(e['t']),side=s,add_fill_qty=q,keys=add_keys[s],
                    repair_debt_before=debt,side_inventory_before=side_inv,opposite_inventory_before=opp_inv,net_gap_before=gap,
                    pressure_inventory=pressure_inventory,pressure_gap=pressure_gap,coverage_weight=coverage_weight,
                    paid_since_previous_same_side_add=prev_paid,born_since_previous_same_side_add=prev_born,service_ratio=service_ratio,
                    debt_after=float((e.get('outstanding_after') or {}).get(s,0.0)),winner=row['winner'],our_actual=row['our']['actual'],
                    our_worst=min(row['our']['up'],row['our']['down']),our_best=max(row['our']['up'],row['our']['down']),our_win=row['our']['actual']>0)
                events.append(ev);market_events.append(ev);last_add_index[s]=ei;paid_between[s]=0.0;born_between[s]=0.0
        if market_events:
            qty=sum(e['add_fill_qty'] for e in market_events); wp=sum(e['coverage_weight']*e['add_fill_qty'] for e in market_events)/qty
            high=sum(e['add_fill_qty'] for e in market_events if e['pressure_inventory']>=0.5)/qty
            very=sum(e['add_fill_qty'] for e in market_events if e['pressure_inventory']>=0.75)/qty
            markets.append(dict(market_id=mid,winner=row['winner'],our_actual=row['our']['actual'],our_worst=min(row['our']['up'],row['our']['down']),
                our_best=max(row['our']['up'],row['our']['down']),our_win=row['our']['actual']>0,add_event_count=len(market_events),add_fill_qty=qty,
                qty_weighted_coverage=wp,high_pressure_add_share=high,very_high_pressure_add_share=very,
                max_pressure=max(e['pressure_inventory'] for e in market_events),median_pressure=med([e['pressure_inventory'] for e in market_events])))
    # Event-level descriptive bins only; bins are analytical summaries, not policy thresholds.
    bins=[(0,.25),(.25,.5),(.5,.75),(.75,1.0000001),(1.0000001,float('inf'))]; bs=[]
    for lo,hi in bins:
        z=[e for e in events if lo<=e['pressure_inventory']<hi]
        bs.append(dict(lo=lo,hi=None if math.isinf(hi) else hi,n=len(z),add_qty=sum(e['add_fill_qty'] for e in z),avg_market_actual=mean([e['our_actual'] for e in z]),avg_market_worst=mean([e['our_worst'] for e in z]),win_event_share=mean([1.0 if e['our_win'] else 0.0 for e in z])))
    out=dict(version='BTC5M_V49_REPAIR_PRESSURE_ADD_SHADOW_V1',status='COMPLETE_READ_ONLY_SHADOW',markets=len(markets),events=len(events),
        definitions=dict(repair_debt='atomic outstanding responsibility on the ADD side immediately before an actual ADD fill; this debt requires opposite-side confirmed repair',
            pressure_inventory='repair_debt_before / confirmed ADD-side inventory immediately before fill',
            coverage_weight='max(0,min(1,1-pressure_inventory)); diagnostic candidate only, not a deployed sizing rule',
            service_ratio='confirmed repair paid / new debt born since previous same-side actual ADD fill; event-driven, no fixed-second window'),
        market_correlations=dict(coverage_vs_actual_pnl=spearman([m['qty_weighted_coverage'] for m in markets],[m['our_actual'] for m in markets]),
            high_pressure_share_vs_actual_pnl=spearman([m['high_pressure_add_share'] for m in markets],[m['our_actual'] for m in markets]),
            high_pressure_share_vs_worst_branch=spearman([m['high_pressure_add_share'] for m in markets],[m['our_worst'] for m in markets])),
        event_pressure_bins=bs,
        market_summary=sorted(markets,key=lambda x:x['our_actual']),
        highest_pressure_events=sorted(events,key=lambda x:(x['pressure_inventory'],x['add_fill_qty']),reverse=True)[:100])
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:out[k] for k in ('status','markets','events','market_correlations','event_pressure_bins')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
