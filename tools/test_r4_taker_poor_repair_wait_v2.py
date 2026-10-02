from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_overrepair_v1 as base
from tools import hftbacktest_r3_context_control_overrepair_v1 as r3ctl
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
EPS=1e-9

def run_wait(mid:int):
    events=[]; used={'v':False}
    def hook(**kw):
        if used['v']: return False
        c=kw['c']; side=str(kw['side']); price=float(kw['price']); qty=float(kw['qty']); now=int(kw['now'])
        up=float(c.inventory.maker_up+c.inventory.taker_up); dn=float(c.inventory.maker_down+c.inventory.taker_down)
        upc=float(c.inventory.maker_up_cost+c.inventory.taker_up_cost); dnc=float(c.inventory.maker_down_cost+c.inventory.taker_down_cost)
        gap=up-dn; weak='DOWN' if gap>EPS else 'UP' if gap<-EPS else None
        if not weak or side!=weak: return False
        strong_sh=up if gap>0 else dn; strong_cost=upc if gap>0 else dnc
        strong_avg=strong_cost/strong_sh if strong_sh>EPS else 0.0
        pair_cost=float(strong_avg+price)
        nup=up+(qty if side=='UP' else 0.0); ndn=dn+(qty if side=='DOWN' else 0.0)
        post_floor=min(nup,ndn)-(upc+dnc+qty*price)
        pre_floor=min(up,dn)-(upc+dnc)
        if pair_cost>1.0+EPS and post_floor<=0.0+EPS:
            used['v']=True
            events.append({'marketId':mid,'atMs':now,'decisionId':kw.get('decision_id'),'side':side,'observedAsk':price,'maxPrice':float(kw['max_price']),'qty':qty,'preGap':gap,'preFloor':pre_floor,'postFloorIfFilledAtObservedAsk':post_floor,'strongAvgCostProxy':strong_avg,'marginalPairCostProxy':pair_cost,'predEffect':kw.get('pred_effect'),'action':'WAIT_ONCE_PRE_SUBMIT'})
            return True
        return False
    old=base.TAKER_PRE_SUBMIT_HOOK; base.TAKER_PRE_SUBMIT_HOOK=hook
    try: rep=r3ctl.run_market(mid,True)
    finally: base.TAKER_PRE_SUBMIT_HOOK=old
    rep['takerPoorRepairWaitV2']=events; return rep

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ids-json',required=True); ap.add_argument('--data-root',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    dr=Path(a.data_root).resolve(); base.STRATEGY_DB=dr/'strategy_target_compare_v1.db'; base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db'; ex.BOOK_DB=dr/'wallet_maker_book_inference.db'; tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'; settle=dr/'target_wallet_official_v1.db'
    ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())]; rows=[]; errs=[]
    def winner(mid):
        c=sqlite3.connect(settle); r=c.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone(); c.close()
        if not r or str(r[0]) not in {'UP','DOWN'}: raise RuntimeError(f'no settlement {mid}')
        return str(r[0])
    for mid in ids:
        try:
            A=r3ctl.run_market(mid,True); B=run_wait(mid); w=winner(mid); sa=score(A,w); sb=score(B,w); ev=B.get('takerPoorRepairWaitV2') or []
            row={'marketId':mid,'R3':sa,'waitOnce':sb,'deltaPnl':sb['pnlUsdt']-sa['pnlUsdt'],'deltaFloor':sb['finalFloor']-sa['finalFloor'],'deltaAbsNet':sb['finalAbsNet']-sa['finalAbsNet'],'intervention':ev[0] if ev else None,'baseTakerFills':len(A.get('takerEvents') or []),'waitTakerFills':len(B.get('takerEvents') or []),'baseTakerAttempts':len(A.get('takerAttempts') or []),'waitTakerAttempts':len(B.get('takerAttempts') or [])}; rows.append(row); print(json.dumps({'marketId':mid,'intervened':bool(ev),'deltaPnl':row['deltaPnl'],'deltaFloor':row['deltaFloor'],'baseTakerFills':row['baseTakerFills'],'waitTakerFills':row['waitTakerFills']},ensure_ascii=False),flush=True)
        except Exception as e:
            errs.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'}); print(json.dumps(errs[-1]),flush=True)
    z=[r for r in rows if r['intervention'] is not None]; ds=[r['deltaPnl'] for r in z]
    out={'version':'R4_TAKER_POOR_REPAIR_WAIT_V2','researchOnly':True,'rows':rows,'errors':errs,'aggregate':{'markets':len(rows),'errors':len(errs),'interventionMarkets':len(z),'totalDeltaPnlIntervened':float(sum(ds)),'improvements':sum(x>1e-9 for x in ds),'degradations':sum(x<-1e-9 for x in ds),'ties':sum(abs(x)<=1e-9 for x in ds),'worstDeltaPnl':min(ds,default=None),'bestDeltaPnl':max(ds,default=None),'meanDeltaFloorIntervened':float(np.mean([r['deltaFloor'] for r in z])) if z else None,'meanDeltaAbsNetIntervened':float(np.mean([r['deltaAbsNet'] for r in z])) if z else None}}; Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
