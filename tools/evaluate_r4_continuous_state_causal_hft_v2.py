from __future__ import annotations
import argparse,json,sqlite3,sys,os
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def configure(root:Path):
    base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'

def run_branch(mid:int,mode:str,lat:list[int]):
    old_shares=float(base.mod.SHARES);old_run=base.run_market
    def wrap(market_id:int,*args,**kwargs):
        kwargs['taker_sizing_mode']='r3_rawq';kwargs['prewrite_state_mode']=mode;kwargs['strategy_compute_latency_profile_ms']=list(lat);return old_run(market_id,*args,**kwargs)
    base.mod.SHARES=10.0;base.run_market=wrap
    try:return r3ctl.run_market(int(mid),True)
    finally:base.run_market=old_run;base.mod.SHARES=old_shares

def compact(rep):
    s=rep.get('studentRollout') or {};p=s.get('finalPortfolio') or {};ev=rep.get('prewriteStateEvents') or []
    return {'makerFilledShares':float(s.get('makerFilledShares') or 0),'takerFilledShares':float(s.get('takerFilledShares') or 0),'makerPlacements':int(s.get('makerPlacements') or 0),'finalFloor':float(p.get('worst_case_floor') or 0),'finalAbsNet':float(p.get('combined_abs_net') or 0),'hardInvalidates':sum(x.get('result')=='HARD_INVALIDATE_REDECIDE' for x in ev),'bookRevalidated':sum(bool(x.get('bookRevalidated')) for x in ev),'prewriteEvents':len(ev)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--latency-profile',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();configure(root);ids=[int(x) for x in a.ids.split(',') if x.strip()]
    raw=json.loads(Path(a.latency_profile).read_text(encoding='utf-8'));lat=(raw.get('stepTotalMs') if isinstance(raw,dict) else raw) or [];lat=[int(round(float(x))) for x in lat]
    settle=root/'target_wallet_official_v1.db';rows=[];names=['LOCK_COUPLED_STALE','ASYNC_STATE_ONLY','ASYNC_CONTINUOUS']
    for mid in ids:
        rec={'marketId':mid,'branches':{}}
        try:
            con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
            if winner not in {'UP','DOWN'}:raise RuntimeError('winner_missing')
            rec['winner']=winner
            for name in names:
                r=run_branch(mid,name,lat);rec['branches'][name]={'score':score(r,winner),'compact':compact(r),'events':r.get('prewriteStateEvents') or []}
            bp=rec['branches']['LOCK_COUPLED_STALE']['score']['pnlUsdt']
            for name in names[1:]:
                cp=rec['branches'][name]['score']['pnlUsdt'];rec['branches'][name]['deltaPnl']=cp-bp;rec['branches'][name]['conversion']=('WIN' if bp>0 else 'LOSS')+'->'+('WIN' if cp>0 else 'LOSS')
        except Exception as e:rec['error']=f'{type(e).__name__}:{e}'
        rows.append(rec);print(json.dumps({'marketId':mid,'error':rec.get('error'),'stateOnly':rec.get('branches',{}).get('ASYNC_STATE_ONLY',{}).get('deltaPnl'),'continuous':rec.get('branches',{}).get('ASYNC_CONTINUOUS',{}).get('deltaPnl')},ensure_ascii=False),flush=True)
    ok=[r for r in rows if not r.get('error')];summary={}
    for name in names:
        z=[r['branches'][name] for r in ok];pn=[x['score']['pnlUsdt'] for x in z];c=[x['compact'] for x in z]
        summary[name]={'n':len(z),'wins':sum(v>0 for v in pn),'pnl':sum(pn),'makerShares':sum(x['makerFilledShares'] for x in c),'takerShares':sum(x['takerFilledShares'] for x in c),'makerPlacements':sum(x['makerPlacements'] for x in c),'finalAbsNetMean':sum(x['finalAbsNet'] for x in c)/len(c) if c else None,'finalFloorMean':sum(x['finalFloor'] for x in c)/len(c) if c else None,'hardInvalidates':sum(x['hardInvalidates'] for x in c),'bookRevalidated':sum(x['bookRevalidated'] for x in c)}
    for name in names[1:]:
        ds=[r['branches'][name]['deltaPnl'] for r in ok];summary[name+'_VS_STALE']={'deltaPnl':sum(ds),'improve':sum(x>1e-9 for x in ds),'degrade':sum(x<-1e-9 for x in ds),'tie':sum(abs(x)<=1e-9 for x in ds),'conversions':dict(Counter(r['branches'][name]['conversion'] for r in ok))}
    out={'version':'R4_CONTINUOUS_STATE_CAUSAL_HFT_V2','researchOnly':True,'liveMutation':False,'meaning':{'ASYNC_STATE_ONLY':'continuous execution/obligation/carrier state; stale Maker action hard-invalidated; book-only change preserves original quote economics','ASYNC_CONTINUOUS':'same plus latest-book requote/revalidation'},'summary':summary,'rows':rows}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(summary,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
