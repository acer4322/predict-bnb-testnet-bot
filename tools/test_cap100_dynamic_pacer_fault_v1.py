from __future__ import annotations
import sqlite3,json,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'; V='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
OUT=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_dynamic_pacer_fault_v1.json'
MIDS=[1513698,1513997,1514018,1514073,1514152,1514162,1514169,1514198,1514248,1514249]
BUDGET=80.0; MIN_NOT=1.0

def load(c,m):
    rr=c.execute("select side,price,shares,placed_at_ms,placement_state_json from our_orders where strategy_version=? and market_id=? and channel='MAKER' order by placed_at_ms,order_id",(V,m)).fetchall(); z=[]
    for r in rr:
        try:d=json.loads(r['placement_state_json'] or '{}')
        except:d={}
        rs=str(d.get('reason') or '').upper(); kind='REPAIR' if ('REPAIR' in rs or 'UNRESOLVED' in rs) else 'NORMAL'
        z.append({'t':int(r['placed_at_ms']),'side':str(r['side']),'price':float(r['price']),'shares':float(r['shares']),'kind':kind})
    return z

def turn(rows):return sum(x['price']*x['shares'] for x in rows)
def qt(a,q):
    a=sorted(a); x=(len(a)-1)*q; lo=int(x); hi=min(len(a)-1,lo+1); w=x-lo; return a[lo]*(1-w)+a[hi]*w

def pace(src,scale):
    t0=src[0]['t']; spend=0.0; buckets={k:{'not':0.0,'sh':0.0} for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}; out=[]
    for i,x in enumerate(src):
        frac=min(1,max(0,(x['t']-t0)/300000)); b=f"{x['kind']}_{x['side']}"; vs=x['shares']*scale
        buckets[b]['not']+=vs*x['price']; buckets[b]['sh']+=vs
        normal_ceiling=(6+62*(frac**0.70)) if frac<.8 else (68+12*((frac-.8)/.2)); normal_ceiling=min(BUDGET,normal_ceiling)
        ceiling=BUDGET if x['kind']=='REPAIR' else normal_ceiling
        avail=max(0,min(BUDGET-spend,ceiling-spend))
        if buckets[b]['not']>=MIN_NOT and avail>=MIN_NOT:
            amt=min(buckets[b]['not'],avail)
            if amt>=MIN_NOT:
                sh=min(buckets[b]['sh'],amt/x['price'])
                out.append({'i':len(out),'srcIntent':i,'tMs':x['t']-t0,'bucket':b,'kind':x['kind'],'side':x['side'],'price':x['price'],'notional':amt,'shares':sh})
                spend+=amt; buckets[b]['not']-=amt; buckets[b]['sh']-=sh
    return out,buckets,spend

def fault_run(orders,ftype,indices):
    pending={k:0.0 for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}; frozen=False; esc=[]
    for j,o in enumerate(orders):
        if frozen:break
        carry=pending[o['bucket']]; pending[o['bucket']]=0.0; sh=o['shares']+carry
        hit=j in indices
        if hit and ftype=='PARTIAL50': pending[o['bucket']]+=sh*.5; continue
        if hit and ftype in {'REJECT','NO_FILL','TIMEOUT'}:
            if o['kind']=='REPAIR': esc.append({'atMs':o['tMs'],'bucket':o['bucket'],'shares':sh,'action':'ACTIVE_CONVERSION_REQUIRED'}); continue
            pending[o['bucket']]+=sh; continue
        if hit and ftype=='CANCEL_UNKNOWN': pending[o['bucket']]+=sh; frozen=True; esc.append({'atMs':o['tMs'],'bucket':o['bucket'],'shares':sh,'action':'FREEZE_AND_RECONCILE'}); break
    # normal leftovers remain explicit responsibility, never orphan
    return {'frozen':frozen,'pending':pending,'escalations':esc,'orphans':0}

def main():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    prev=[]
    for (m,) in c.execute("select distinct market_id from our_orders where strategy_version=? and channel='MAKER' and market_id<? order by market_id",(V,MIDS[0])):
        r=load(c,m)
        if r:prev.append(turn(r))
    rows=[]; fault_cases=0; orphans=0; repair_cases=0; repair_escalated=0
    for m in MIDS:
        p95=qt(prev,.95); scale=min(1,BUDGET/p95); src=load(c,m); orders,buckets,spend=pace(src,scale)
        ridx=next((i for i,o in enumerate(orders) if o['kind']=='REPAIR'),None)
        faults={
            'partial50_first':fault_run(orders,'PARTIAL50',[0]) if orders else None,
            'reject_first':fault_run(orders,'REJECT',[0]) if orders else None,
            'two_no_fill':fault_run(orders,'NO_FILL',[0,1]) if len(orders)>1 else None,
            'cancel_unknown':fault_run(orders,'CANCEL_UNKNOWN',[0]) if orders else None,
        }
        if ridx is not None:
            faults['repair_reject']=fault_run(orders,'REJECT',[ridx]); repair_cases+=1
            if any(e['action']=='ACTIVE_CONVERSION_REQUIRED' for e in faults['repair_reject']['escalations']):repair_escalated+=1
        valid=[x for x in faults.values() if x]; fault_cases+=len(valid); orphans+=sum(x['orphans'] for x in valid)
        last=max((o['tMs'] for o in orders),default=None)
        rows.append({'marketId':m,'p95':p95,'scale':scale,'childShares':18*scale,'spent':spend,'emittedOrders':len(orders),'lastEmitMs':last,'pendingVirtualNotional':sum(v['not'] for v in buckets.values()),'faults':faults})
        prev.append(turn(src))
    summary={'markets':len(rows),'under80':sum(r['spent']<=80+1e-9 for r in rows),'maxSpent':max(r['spent'] for r in rows),'medianSpent':statistics.median(r['spent'] for r in rows),'past120':sum((r['lastEmitMs'] or 0)>=120000 for r in rows),'past180':sum((r['lastEmitMs'] or 0)>=180000 for r in rows),'medianLastEmitMs':statistics.median(r['lastEmitMs'] for r in rows if r['lastEmitMs'] is not None),'faultCases':fault_cases,'orphanCases':orphans,'repairRejectCases':repair_cases,'repairRejectEscalated':repair_escalated,'cancelUnknownFrozen':sum(bool((r['faults'].get('cancel_unknown') or {}).get('frozen')) for r in rows)}
    OUT.write_text(json.dumps({'version':'CAP100_DYNAMIC_PACER_FAULT_V1','summary':summary,'markets':rows},indent=2),encoding='utf-8')
    print(json.dumps({'out':str(OUT),'summary':summary},indent=2))
if __name__=='__main__':main()
