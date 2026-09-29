from __future__ import annotations
import json, math, sqlite3, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'
OUT=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_v2_multimarket_shape_fault_v0.json'
VER='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
MIN_NOTIONAL=1.0
MAKER_BUDGET=80.0
BASE_SHARES=18.0
TEST_START=1513698
TEST_N=10

def qtile(xs,q):
    ys=sorted(xs)
    if not ys:return None
    pos=(len(ys)-1)*q; lo=int(math.floor(pos)); hi=int(math.ceil(pos))
    if lo==hi:return ys[lo]
    w=pos-lo; return ys[lo]*(1-w)+ys[hi]*w

def parse_reason(s):
    try:d=json.loads(s or '{}')
    except:d={}
    r=str(d.get('reason') or '').upper()
    return 'REPAIR' if 'REPAIR' in r or 'UNRESOLVED' in r else 'NORMAL'

def load_market(con,mid):
    rows=con.execute("select side,price,shares,placed_at_ms,placement_state_json from our_orders where strategy_version=? and market_id=? and channel='MAKER' order by placed_at_ms,order_id",(VER,mid)).fetchall()
    out=[]
    for r in rows:
        out.append({'side':r[0],'price':float(r[1]),'shares':float(r[2]),'t':int(r[3]),'kind':parse_reason(r[4])})
    return out

def turnover(rows): return sum(x['price']*x['shares'] for x in rows)

def compress(rows,scale):
    if not rows:return {'orders':[],'leftovers':{},'virtual':0.0}
    t0=rows[0]['t']; buckets={k:{'notional':0.0,'shares':0.0} for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}; emitted=[]; virt=0.0
    for i,x in enumerate(rows):
        bucket=f"{x['kind']}_{x['side']}"; vs=x['shares']*scale; vn=vs*x['price']; virt+=vn
        b=buckets[bucket]; b['notional']+=vn; b['shares']+=vs
        if b['notional']+1e-12>=MIN_NOTIONAL:
            # preserve accumulated share semantics; current price only sets executable notional check
            emit_sh=b['shares']; emit_not=emit_sh*x['price']
            if emit_not+1e-12>=MIN_NOTIONAL:
                emitted.append({'i':len(emitted),'srcIntent':i,'tMs':x['t']-t0,'side':x['side'],'kind':x['kind'],'bucket':bucket,'price':x['price'],'shares':emit_sh,'notional':emit_not})
                b['notional']=0.0;b['shares']=0.0
    return {'orders':emitted,'leftovers':{k:v['shares'] for k,v in buckets.items()},'virtual':virt}

def carry_fault(orders,fault):
    pending={k:0.0 for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}; frozen=False; fills=[]; escal=[]
    fault_idx=fault.get('idx'); second=fault.get('idx2'); ftype=fault['type']
    for j,o in enumerate(orders):
        if frozen: break
        carry=pending[o['bucket']]; pending[o['bucket']]=0.0; sh=o['shares']+carry
        hit=j==fault_idx or j==second
        if hit and ftype=='PARTIAL50':
            filled=sh*0.5; pending[o['bucket']]+=sh-filled; fills.append({**o,'shares':filled}); continue
        if hit and ftype in {'REJECT','NO_FILL','TIMEOUT'}:
            pending[o['bucket']]+=sh; continue
        if hit and ftype=='CANCEL_UNKNOWN':
            pending[o['bucket']]+=sh; frozen=True; break
        fills.append({**o,'shares':sh})
    # observable carrier semantics: repair pending => active conversion; normal pending => carry/terminal; cancel unknown => reconcile
    for k,v in pending.items():
        if v<=1e-9: continue
        if frozen: escal.append({'bucket':k,'shares':v,'action':'FREEZE_AND_RECONCILE'})
        elif k.startswith('REPAIR_'): escal.append({'bucket':k,'shares':v,'action':'ACTIVE_CONVERSION_REQUIRED'})
        else: escal.append({'bucket':k,'shares':v,'action':'CARRY_FORWARD_OR_TERMINAL_RISK_REDUCTION'})
    return {'filledOrders':len(fills),'frozen':frozen,'pending':pending,'escalations':escal,'orphans':0}

def main():
    con=sqlite3.connect(DB)
    mids=[r[0] for r in con.execute("select distinct market_id from our_orders where strategy_version=? and channel='MAKER' and market_id>=? order by market_id limit ?",(VER,TEST_START,TEST_N)).fetchall()]
    # strict-past calibration set only markets before each test market
    all_prev=[r[0] for r in con.execute("select distinct market_id from our_orders where strategy_version=? and channel='MAKER' and market_id<? order by market_id",(VER,TEST_START)).fetchall()]
    prev_turn=[turnover(load_market(con,m)) for m in all_prev if load_market(con,m)]
    rows=[]
    for mid in mids:
        p75=qtile(prev_turn,0.75); scale=min(1.0,MAKER_BUDGET/p75) if p75 and p75>0 else 1.0
        src=load_market(con,mid); comp=compress(src,scale); eo=comp['orders']
        repair_idx=next((i for i,o in enumerate(eo) if o['kind']=='REPAIR'), None)
        faults=[
            {'name':'partial50_first','type':'PARTIAL50','idx':0},
            {'name':'reject_first','type':'REJECT','idx':0},
            {'name':'two_nofill_same_start','type':'NO_FILL','idx':0,'idx2':1 if len(eo)>1 and eo[1]['side']==eo[0]['side'] else None},
            {'name':'cancel_unknown_first','type':'CANCEL_UNKNOWN','idx':0},
        ]
        if repair_idx is not None:faults.append({'name':'repair_reject','type':'REJECT','idx':repair_idx})
        fr={f['name']:carry_fault(eo,f) for f in faults}
        last_t=max((o['tMs'] for o in eo),default=None)
        rows.append({'marketId':mid,'sourceOrders':len(src),'sourceTurnover':turnover(src),'strictPastP75Turnover':p75,'scale':scale,'nominalChildShares':BASE_SHARES*scale,'emittedOrders':len(eo),'emittedNotional':sum(o['notional'] for o in eo),'lastEmitMs':last_t,'repairEmitted':sum(o['kind']=='REPAIR' for o in eo),'leftoverShares':comp['leftovers'],'faults':fr})
        prev_turn.append(turnover(src))
    summary={
        'markets':len(rows),
        'allBaselineUnder80':all(r['emittedNotional']<=80.000001 for r in rows),
        'baselineUnder80Count':sum(r['emittedNotional']<=80.000001 for r in rows),
        'medianLastEmitMs':statistics.median([r['lastEmitMs'] for r in rows if r['lastEmitMs'] is not None]),
        'marketsEmitPast120s':sum((r['lastEmitMs'] or 0)>=120000 for r in rows),
        'marketsEmitPast180s':sum((r['lastEmitMs'] or 0)>=180000 for r in rows),
        'faultCases':sum(len(r['faults']) for r in rows),
        'orphanCases':sum(any(x['orphans'] for x in r['faults'].values()) for r in rows),
        'repairRejectCases':sum('repair_reject' in r['faults'] for r in rows),
        'repairRejectEscalated':sum('repair_reject' in r['faults'] and any(e['action']=='ACTIVE_CONVERSION_REQUIRED' for e in r['faults']['repair_reject']['escalations']) for r in rows),
        'cancelUnknownFrozen':sum(r['faults']['cancel_unknown_first']['frozen'] for r in rows),
    }
    report={'version':'CAP100_V2_MULTIMARKET_SHAPE_FAULT_V0','boundary':'Historical R2 intent-stream structural replay only; not HFT fill/PnL evidence and not a true policy closed-loop because later R2 intents are frozen. Strict-past scaling per market; minimum-$1 responsibility-aware compression; observable-carrier fault semantics.','summary':summary,'markets':rows}
    OUT.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'out':str(OUT),'summary':summary,'markets':[{'marketId':r['marketId'],'emittedNotional':r['emittedNotional'],'lastEmitMs':r['lastEmitMs'],'sourceOrders':r['sourceOrders'],'emittedOrders':r['emittedOrders']} for r in rows]},ensure_ascii=False,indent=2))
    con.close()
if __name__=='__main__':main()
