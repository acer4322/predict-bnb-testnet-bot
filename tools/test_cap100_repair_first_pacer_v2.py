from __future__ import annotations
import sqlite3,json,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from tools.test_cap100_dynamic_pacer_fault_v1 import DB,V,MIDS,BUDGET,load,turn,qt
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_repair_first_pacer_v2.json'
MIN_NOT=1.0

def pace(src,scale):
    t0=src[0]['t']; spend=0.0
    buckets={k:{'not':0.0,'sh':0.0} for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}; out=[]
    for i,x in enumerate(src):
        frac=min(1,max(0,(x['t']-t0)/300000)); b=f"{x['kind']}_{x['side']}"; vs=x['shares']*scale; vn=vs*x['price']
        buckets[b]['not']+=vn; buckets[b]['sh']+=vs
        if x['kind']=='REPAIR':
            # preserve repair timing: if there is any repair responsibility, top up to legal $1 when capital permits
            need=max(MIN_NOT,buckets[b]['not'])
            avail=max(0,BUDGET-spend)
            if avail+1e-12>=need:
                notional=need; shares=notional/x['price']
                out.append({'i':len(out),'srcIntent':i,'tMs':x['t']-t0,'bucket':b,'kind':'REPAIR','side':x['side'],'price':x['price'],'notional':notional,'shares':shares,'virtualNotional':buckets[b]['not'],'topup':max(0,notional-buckets[b]['not'])})
                spend+=notional; buckets[b]={'not':0.0,'sh':0.0}
            continue
        normal_ceiling=(6+60*(frac**0.70)) if frac<.8 else (66+10*((frac-.8)/.2)); normal_ceiling=min(BUDGET,normal_ceiling)
        avail=max(0,min(BUDGET-spend,normal_ceiling-spend))
        if buckets[b]['not']>=MIN_NOT and avail>=MIN_NOT:
            amt=min(buckets[b]['not'],avail)
            if amt>=MIN_NOT:
                sh=min(buckets[b]['sh'],amt/x['price'])
                out.append({'i':len(out),'srcIntent':i,'tMs':x['t']-t0,'bucket':b,'kind':'NORMAL','side':x['side'],'price':x['price'],'notional':amt,'shares':sh,'virtualNotional':buckets[b]['not'],'topup':0.0})
                spend+=amt; buckets[b]['not']-=amt; buckets[b]['sh']-=sh
    return out,buckets,spend

def main():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; prev=[]
    for (m,) in c.execute("select distinct market_id from our_orders where strategy_version=? and channel='MAKER' and market_id<? order by market_id",(V,MIDS[0])):
        r=load(c,m)
        if r:prev.append(turn(r))
    rows=[]
    for m in MIDS:
        p95=qt(prev,.95); scale=min(1,BUDGET/p95); src=load(c,m); out,b,spent=pace(src,scale)
        src_rep=sum(x['shares']*scale for x in src if x['kind']=='REPAIR'); em_rep=sum(x['shares'] for x in out if x['kind']=='REPAIR')
        src_norm=sum(x['shares']*scale for x in src if x['kind']=='NORMAL'); em_norm=sum(x['shares'] for x in out if x['kind']=='NORMAL')
        rows.append({'marketId':m,'spent':spent,'orders':len(out),'lastEmitMs':max((x['tMs'] for x in out),default=None),'repairSourceShares':src_rep,'repairEmittedShares':em_rep,'repairMassRetention':em_rep/src_rep if src_rep else 1.0,'normalMassRetention':em_norm/src_norm if src_norm else 1.0,'repairTopupUsdt':sum(x['topup'] for x in out if x['kind']=='REPAIR'),'repairOrders':sum(x['kind']=='REPAIR' for x in out)})
        prev.append(turn(src))
    summary={'markets':len(rows),'under80':sum(r['spent']<=80+1e-9 for r in rows),'maxSpent':max(r['spent'] for r in rows),'medianSpent':statistics.median(r['spent'] for r in rows),'past120':sum((r['lastEmitMs'] or 0)>=120000 for r in rows),'past180':sum((r['lastEmitMs'] or 0)>=180000 for r in rows),'medianRepairMassRetention':statistics.median(r['repairMassRetention'] for r in rows),'medianNormalMassRetention':statistics.median(r['normalMassRetention'] for r in rows),'totalRepairTopupUsdt':sum(r['repairTopupUsdt'] for r in rows)}
    OUT.write_text(json.dumps({'version':'CAP100_REPAIR_FIRST_PACER_V2','summary':summary,'markets':rows},indent=2),encoding='utf-8')
    print(json.dumps({'out':str(OUT),'summary':summary,'markets':rows},indent=2))
if __name__=='__main__':main()
