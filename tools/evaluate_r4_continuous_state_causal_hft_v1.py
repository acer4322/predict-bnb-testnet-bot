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
    base.STRATEGY_DB=root/'strategy_target_compare_v1.db'
    base.mod.BOOK_DB=root/'wallet_maker_book_inference.db'
    base.ex.BOOK_DB=root/'wallet_maker_book_inference.db'
    base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'

def run_branch(mid:int,mode:str,lat:list[int]):
    old_shares=float(base.mod.SHARES); old_run=base.run_market
    def run_wrap(market_id:int,*args,**kwargs):
        kwargs['taker_sizing_mode']='r3_rawq'
        kwargs['prewrite_state_mode']=str(mode)
        kwargs['strategy_compute_latency_profile_ms']=list(lat)
        return old_run(market_id,*args,**kwargs)
    base.mod.SHARES=10.0; base.run_market=run_wrap
    try:
        return r3ctl.run_market(int(mid),True)
    finally:
        base.run_market=old_run; base.mod.SHARES=old_shares

def compact(rep:dict):
    s=rep.get('studentRollout') or {}; p=s.get('finalPortfolio') or {}; ev=rep.get('prewriteStateEvents') or []
    return {
      'makerFilledShares':float(s.get('makerFilledShares') or 0.0),
      'takerFilledShares':float(s.get('takerFilledShares') or 0.0),
      'makerFillEvents':int(s.get('makerFillEvents') or 0),
      'takerFills':int(s.get('takerFills') or 0),
      'finalFloor':float(p.get('worst_case_floor') or 0.0),
      'finalAbsNet':float(p.get('combined_abs_net') or 0.0),
      'makerPlacements':int(s.get('makerPlacements') or 0),
      'prewriteEvents':len(ev),
      'hardInvalidates':sum(str(x.get('result'))=='HARD_INVALIDATE_REDECIDE' for x in ev),
      'submitted':sum(str(x.get('result'))=='SUBMITTED' for x in ev),
      'bookRevalidated':sum(bool(x.get('bookRevalidated')) for x in ev),
      'meanDelayMs':(sum(float(x.get('delayMs') or 0) for x in ev)/len(ev)) if ev else 0.0,
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--latency-profile',required=True);ap.add_argument('--out',required=True)
    a=ap.parse_args();root=Path(a.data_root).resolve();configure(root);ids=[int(x) for x in a.ids.split(',') if x.strip()]
    lat=json.loads(Path(a.latency_profile).read_text(encoding='utf-8'))
    if isinstance(lat,dict):lat=lat.get('stepTotalMs') or lat.get('latenciesMs') or lat.get('values') or []
    lat=[int(round(float(x))) for x in lat if float(x)>=0]
    if not lat:raise RuntimeError('empty latency profile')
    settle=root/'target_wallet_official_v1.db';rows=[]
    for mid in ids:
      rec={'marketId':mid,'branches':{}}
      try:
        con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
        if winner not in {'UP','DOWN'}:raise RuntimeError('winner_missing')
        for name,mode in [('LOCK_COUPLED_STALE','LOCK_COUPLED_STALE'),('ASYNC_CONTINUOUS','ASYNC_CONTINUOUS')]:
            rep=run_branch(mid,mode,lat);rec['branches'][name]={'score':score(rep,winner),'compact':compact(rep),'events':rep.get('prewriteStateEvents') or []}
        rec['winner']=winner
        bp=rec['branches']['LOCK_COUPLED_STALE']['score']['pnlUsdt'];cp=rec['branches']['ASYNC_CONTINUOUS']['score']['pnlUsdt']
        rec['deltaPnl']=cp-bp;rec['conversion']=('WIN' if bp>0 else 'LOSS')+'->'+('WIN' if cp>0 else 'LOSS')
      except Exception as e:rec['error']=f'{type(e).__name__}:{e}'
      rows.append(rec);print(json.dumps({'marketId':mid,'error':rec.get('error'),'conversion':rec.get('conversion'),'deltaPnl':rec.get('deltaPnl')},ensure_ascii=False),flush=True)
    ok=[r for r in rows if not r.get('error')];summary={}
    for name in ('LOCK_COUPLED_STALE','ASYNC_CONTINUOUS'):
      z=[r['branches'][name] for r in ok]
      summary[name]={
        'n':len(z),'wins':sum(x['score']['pnlUsdt']>0 for x in z),'pnl':sum(x['score']['pnlUsdt'] for x in z),
        'makerShares':sum(x['compact']['makerFilledShares'] for x in z),'takerShares':sum(x['compact']['takerFilledShares'] for x in z),
        'makerPlacements':sum(x['compact']['makerPlacements'] for x in z),'finalFloorMean':(sum(x['compact']['finalFloor'] for x in z)/len(z)) if z else None,
        'finalAbsNetMean':(sum(x['compact']['finalAbsNet'] for x in z)/len(z)) if z else None,
        'hardInvalidates':sum(x['compact']['hardInvalidates'] for x in z),'bookRevalidated':sum(x['compact']['bookRevalidated'] for x in z),
        'prewriteEvents':sum(x['compact']['prewriteEvents'] for x in z),
      }
    summary['ASYNC_VS_STALE']={'deltaPnl':sum(r['deltaPnl'] for r in ok),'conversions':dict(Counter(r['conversion'] for r in ok)),'improve':sum(r['deltaPnl']>1e-9 for r in ok),'degrade':sum(r['deltaPnl']<-1e-9 for r in ok),'tie':sum(abs(r['deltaPnl'])<=1e-9 for r in ok)}
    rep={'version':'R4_CONTINUOUS_STATE_CAUSAL_HFT_V1','researchOnly':True,'liveMutation':False,'makerShares':10.0,'takerSizing':'R3_RAWQ_DYNAMIC_NO_18_CAP','latencyProfileCount':len(lat),'comparison':'same live step-latency profile; LOCK_COUPLED_STALE does not reconcile during compute window, ASYNC_CONTINUOUS reconciles before Maker venue write and hard-invalidates stale execution/obligation/carrier actions; book-only changes are requoted/revalidated','summary':summary,'rows':rows,'errors':[r for r in rows if r.get('error')]}
    p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
