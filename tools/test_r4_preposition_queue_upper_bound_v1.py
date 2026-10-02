from __future__ import annotations
import argparse,json,lzma,math
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_execution_shift_audit_v0 as ex
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=ROOT/'data/research/r4_v0/hourly'
LEADS=(0,500,1000,2000,3000,5000)
EPS=1e-9

def choose_files(max_markets:int):
    # newest usable R2 residual paper per market
    files=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    seen=set(); out=[]
    for p in files:
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception: continue
        mid=int(d.get('marketId') or 0); student=str(d.get('student') or '')
        if not mid or mid in seen or 'R2_RESIDUAL' not in student or not (d.get('orderMeta') or {}): continue
        if not (ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz').exists(): continue
        seen.add(mid); out.append((p,d))
        if len(out)>=max_markets: break
    return out

def terminal_map(d):
    canc={str(x.get('orderId')):int(x.get('atMs') or 0) for x in (d.get('cancelEvents') or []) if x.get('orderId')}
    fills={}
    for x in d.get('makerFillEvents') or []:
        oid=str(x.get('orderId') or ''); t=int(x.get('atMs') or x.get('observedAtMs') or 0)
        if oid and t: fills[oid]=max(fills.get(oid,0),t)
    end=0
    for k in ('decisionRows','orderStateRows'):
        for x in d.get(k) or []:
            end=max(end,int(x.get('decisionMs') or x.get('checkpointMs') or x.get('atMs') or 0))
    for x in d.get('makerFillEvents') or []: end=max(end,int(x.get('atMs') or x.get('observedAtMs') or 0))
    for x in d.get('cancelEvents') or []: end=max(end,int(x.get('atMs') or 0))
    return canc,fills,end

def snap(bt,num):
    s=ex.order_snapshot(bt,num)
    return {'fill':float(s.get('cumExecQty') or 0.0),'status':s.get('status')}

def run_one(events,meta,o,lead_ms,terminal_ms):
    need=int(o['placedAtMs']); start=max(int(meta['firstReceivedMs']),need-int(lead_ms)); terminal=min(int(meta['lastReceivedMs']),int(terminal_ms))
    if terminal<=start: return None
    bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
    try:
        if not ex.advance_to(bt,start): return None
        rc=ex.submit_native(bt,1,str(o['side']),float(o['price']),float(o['shares']))
        checkpoints=sorted(set([min(max(need,start),terminal),min(max(need+15000,start),terminal),terminal]))
        vals={}
        first_fill=None; prev=0.0
        # advance in <=250ms steps to estimate first-fill time without future labels
        cur=start
        for target in checkpoints:
            while cur<target:
                nxt=min(target,cur+250)
                if not ex.advance_to(bt,nxt): break
                cur=nxt; ss=snap(bt,1)
                if first_fill is None and ss['fill']>EPS: first_fill=cur
                prev=ss['fill']
            vals[target]=snap(bt,1)
        need_t=min(max(need,start),terminal); t15=min(max(need+15000,start),terminal)
        return {'submitRc':rc,'startMs':start,'needMs':need,'terminalMs':terminal,'fillAtNeed':vals[need_t]['fill'],'fillAtNeed15s':vals[t15]['fill'],'fillAtTerminal':vals[terminal]['fill'],'firstFillMs':first_fill,'firstFillDelayFromNeedMs':None if first_fill is None else first_fill-need,'earlyFill':bool(first_fill is not None and first_fill<need)}
    finally: bt.close()

def med(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return median(xs) if xs else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--max-markets',type=int,default=10); ap.add_argument('--max-orders-per-market',type=int,default=30); a=ap.parse_args()
    rows=[]; markets=[]; errors=[]
    for p,d in choose_files(a.max_markets):
        mid=int(d['marketId']); markets.append(mid)
        try: events,_,meta=tape.build_archive_events(mid,trade_offset='mid')
        except Exception as e: errors.append({'marketId':mid,'stage':'feed','error':f'{type(e).__name__}: {e}'}); continue
        canc,fills,end=terminal_map(d)
        oms=[]
        for oid,o0 in (d.get('orderMeta') or {}).items():
            o=dict(o0); o['orderId']=oid; o['placedAtMs']=int(o.get('placedAtMs') or 0)
            if not o['placedAtMs'] or float(o.get('shares') or 0)<=0: continue
            term=canc.get(oid) or fills.get(oid) or end or int(meta['lastReceivedMs'])
            term=max(term,o['placedAtMs']+1)
            oms.append((o,term))
        oms=sorted(oms,key=lambda z:z[0]['placedAtMs'])[:a.max_orders_per_market]
        for o,term in oms:
            rec={'marketId':mid,'orderId':o['orderId'],'side':o['side'],'price':float(o['price']),'shares':float(o['shares']),'placedAtMs':o['placedAtMs'],'terminalMs':term,'leads':{}}
            for lead in LEADS:
                try: rec['leads'][str(lead)]=run_one(events,meta,o,lead,term)
                except Exception as e: rec['leads'][str(lead)]={'error':f'{type(e).__name__}: {e}'}
            rows.append(rec)
        print(json.dumps({'progressMarket':mid,'orders':len(oms),'totalRows':len(rows)},ensure_ascii=False),flush=True)
    agg={}
    base_key='0'
    for lead in LEADS:
        k=str(lead); rr=[]
        for r in rows:
            b=r['leads'].get(base_key); x=r['leads'].get(k)
            if not isinstance(b,dict) or not isinstance(x,dict) or 'fillAtTerminal' not in b or 'fillAtTerminal' not in x: continue
            rr.append((r,b,x))
        total_qty=sum(r['shares'] for r,_,_ in rr)
        agg[k]={
            'orders':len(rr),
            'terminalFilledShares':sum(x['fillAtTerminal'] for _,_,x in rr),
            'terminalRealizationRate':sum(x['fillAtTerminal'] for _,_,x in rr)/total_qty if total_qty else None,
            'need15sFilledShares':sum(x['fillAtNeed15s'] for _,_,x in rr),
            'need15sRealizationRate':sum(x['fillAtNeed15s'] for _,_,x in rr)/total_qty if total_qty else None,
            'deltaTerminalFilledSharesVsReactive':sum(x['fillAtTerminal']-b['fillAtTerminal'] for _,b,x in rr),
            'deltaNeed15sFilledSharesVsReactive':sum(x['fillAtNeed15s']-b['fillAtNeed15s'] for _,b,x in rr),
            'betterTerminalOrders':sum(x['fillAtTerminal']>b['fillAtTerminal']+EPS for _,b,x in rr),
            'worseTerminalOrders':sum(x['fillAtTerminal']<b['fillAtTerminal']-EPS for _,b,x in rr),
            'earlyFillOrders':sum(bool(x.get('earlyFill')) for _,_,x in rr),
            'earlyFillRate':sum(bool(x.get('earlyFill')) for _,_,x in rr)/len(rr) if rr else None,
            'medianFirstFillDelayFromNeedMs':med([x.get('firstFillDelayFromNeedMs') for _,_,x in rr]),
        }
    report={'version':'R4_PREPOSITION_QUEUE_UPPER_BOUND_V1','researchOnly':True,'causalStatus':'MECHANICAL_QUEUE_OPTIONALITY_UPPER_BOUND_NOT_RUNTIME_POLICY','guards':{'dreamFill':False,'feed':'Execution Tape V1 L2 + true matches','hftbacktest':True,'queueModel':'risk','entryLatencyMs':1092,'responseLatencyMs':273,'futureDecisionUsedOnlyToShiftSameFrozenOrderEarlier':True,'winnerUsed':False,'liveTradingChanges':False},'markets':markets,'errors':errors,'aggregate':agg,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True); path=OUT/'r4_preposition_queue_upper_bound_v1.json'; path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'markets':len(markets),'orders':len(rows),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
