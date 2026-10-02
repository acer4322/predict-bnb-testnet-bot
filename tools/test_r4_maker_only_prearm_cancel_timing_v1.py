from __future__ import annotations
import argparse, json, lzma, math, sqlite3, sys, os
from collections import Counter
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as base

LEADS=(500,1000,3000)
POLICIES=('KEEP','RESP_RELATION','RESP_OWNER')
SETTLE=ROOT/'data/target_wallet_official_v1.db'

def winner_for(mid:int):
    if not SETTLE.exists(): return None
    con=sqlite3.connect(SETTLE)
    try:
        r=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(int(mid),)).fetchone()
        return str(r[0]).upper() if r and r[0] else None
    finally: con.close()

def score_terminal(final,winner):
    if winner not in {'UP','DOWN'}: return None
    payout=float(final['up'] if winner=='UP' else final['down'])
    pnl=payout-float(final['cost'])
    return {'winner':winner,'payoutUsdt':payout,'costUsdt':float(final['cost']),'pnlUsdt':pnl,'positive':bool(pnl>0)}

def md(xs):
    xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return median(xs) if xs else None

def choose_slice(max_markets,offset):
    ds=base.choose_files(max_markets+offset)
    return ds[offset:offset+max_markets]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--max-markets',type=int,default=6); ap.add_argument('--offset',type=int,default=0); ap.add_argument('--out',required=True); a=ap.parse_args()
    ds=choose_slice(a.max_markets,a.offset); rows=[]; errs=[]
    configs=[(0,'REACTIVE')]+[(l,p) for l in LEADS for p in POLICIES]
    orig_prep=base.prep
    def prep_no_taker(d,meta,lead):
        orders,_takers,dec=orig_prep(d,meta,lead)
        return orders,[],dec
    base.prep=prep_no_taker
    for d in ds:
        mid=int(d['marketId']); w=winner_for(mid)
        for lead,pol in configs:
            try:
                r=base.simulate(d,lead,pol)
                r['winner']=w; r['score']=score_terminal(r['final'],w); rows.append(r)
            except Exception as e: errs.append({'marketId':mid,'leadMs':lead,'policy':pol,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'marketId':mid,'winner':w,'rows':len(rows),'errors':len(errs)}),flush=True)
    base.prep=orig_prep
    agg={}
    for lead,pol in configs:
        rr=[r for r in rows if r['leadMs']==lead and r['policy']==pol and r.get('score')]
        wins=sum(1 for r in rr if r['score']['positive']); pnl=sum(r['score']['pnlUsdt'] for r in rr)
        key=f'{pol}_{lead}'
        agg[key]={'markets':len(rr),'wins':wins,'winRate':wins/len(rr) if rr else None,'totalPnl':pnl,
                  'meanPnl':pnl/len(rr) if rr else None,'makerFilledShares':sum(r['makerFilledShares'] for r in rr),
                  'earlyMakerFillShares':sum(r['earlyMakerFillShares'] for r in rr),'earlyFloorDamage':sum(r['earlyFloorDamage'] for r in rr),
                  'prearmedOrders':sum(r['prearmedOrders'] for r in rr),'prunedOrders':sum(r['prunedOrders'] for r in rr),
                  'fallbackOrders':sum(r['fallbackOrders'] for r in rr),'medianFinalFloor':md([r['final']['floor'] for r in rr]),
                  'medianFinalAbsNet':md([r['final']['absNet'] for r in rr]),'durableBaseMarkets':sum(r['durableBase'] for r in rr)}
    # paired sign-change diagnostics vs no-taker reactive
    by={}
    for r in rows:
        if not r.get('score'): continue
        by.setdefault(r['marketId'],{})[(r['leadMs'],r['policy'])]=r
    sign={}
    for lead,pol in configs[1:]:
        z=[]
        for mid,m in by.items():
            b=m.get((0,'REACTIVE')); x=m.get((lead,pol))
            if not b or not x: continue
            a0=b['score']['positive']; a1=x['score']['positive']
            if a0!=a1:
                z.append({'marketId':mid,'conversion':('WIN' if a0 else 'LOSS')+'->'+('WIN' if a1 else 'LOSS'),'baselinePnl':b['score']['pnlUsdt'],'policyPnl':x['score']['pnlUsdt'],'deltaPnl':x['score']['pnlUsdt']-b['score']['pnlUsdt']})
        sign[f'{pol}_{lead}']=z
    rep={'version':'R4_MAKER_ONLY_PREARM_CANCEL_TIMING_V1','researchOnly':True,'actionAuthority':False,
         'semantics':'Zero-Taker Maker-only mechanism test. Frozen Maker intentions may be pre-positioned by 0.5/1/3s. Responsibility policies can cancel pre-need orders when weak-side responsibility is lost / MPQ breaks / duplicate ownership is detected. Winner is post-terminal scoring only.',
         'guards':{'noTaker':True,'dreamFill':False,'realisticHFT':True,'entryLatencyMs':1092,'responseLatencyMs':273,'queueModel':'risk','winnerRuntimeInput':False,'liveChanges':False},
         'aggregate':agg,'signChangesVsReactive':sign,'errors':errs,'rows':rows}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'aggregate':agg,'errors':errs[:5]},indent=2),flush=True)
if __name__=='__main__': main()
