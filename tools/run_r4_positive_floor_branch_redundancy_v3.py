from __future__ import annotations
import argparse,json,sqlite3,sys,warnings
from pathlib import Path
warnings.filterwarnings('ignore', message='X does not have valid feature names')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_r4_complexity_pruning_guard_ablation_v2 as g
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    data=Path(args.data_root).resolve(); ids=[int(x) for x in json.loads(Path(args.ids_json).read_text())]
    g.v5.base.STRATEGY_DB=data/'strategy_target_compare_v1.db';g.v5.base.mod.BOOK_DB=data/'wallet_maker_book_inference.db';g.v5.base.ex.BOOK_DB=data/'wallet_maker_book_inference.db';g.v5.base.tape_v1.ARCHIVE_DIR=data/'execution_tape_v1/markets'
    settle=data/'target_wallet_official_v1.db'
    rows=[]; ev_core=[];ev_nofloor=[]
    for mid in ids:
        rec={'marketId':mid}
        try:
            con=sqlite3.connect(settle); rr=con.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close()
            if not rr or str(rr[0]) not in {'UP','DOWN'}: raise RuntimeError(f'no settlement {mid}')
            winner=str(rr[0]);
            core=g.run_overlay(mid,'LITE_CORE',ev_core); nf=g.run_overlay(mid,'LITE_NO_POSITIVE_FLOOR',ev_nofloor)
            A=score(core,winner);B=score(nf,winner)
            rec.update({'winner':winner,'core':A,'noPositiveFloor':B,'deltaNoFloorVsCorePnl':B['pnlUsdt']-A['pnlUsdt'],'deltaNoFloorVsCoreFloor':B['finalFloor']-A['finalFloor'],'deltaNoFloorVsCoreAbsNet':B['finalAbsNet']-A['finalAbsNet'],'coreStats':core.get('mgmtStats') or {},'noFloorStats':nf.get('mgmtStats') or {}})
        except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec);print(json.dumps({'marketId':mid,'dPnl':rec.get('deltaNoFloorVsCorePnl'),'error':rec.get('error')},ensure_ascii=False),flush=True)
    good=[r for r in rows if 'error' not in r]
    agg={'markets':len(good),'errors':len(rows)-len(good),'pnlDifferentMarkets':sum(abs(r['deltaNoFloorVsCorePnl'])>1e-9 for r in good),'floorDifferentMarkets':sum(abs(r['deltaNoFloorVsCoreFloor'])>1e-9 for r in good),'totalDeltaPnl':sum(r['deltaNoFloorVsCorePnl'] for r in good),'worstDeltaPnl':min([r['deltaNoFloorVsCorePnl'] for r in good],default=None),'bestDeltaPnl':max([r['deltaNoFloorVsCorePnl'] for r in good],default=None),'corePositiveFloorPreserves':sum(int((r.get('coreStats') or {}).get('positiveFloorPreserves',0)) for r in good),'coreCrossOverrides':sum(int((r.get('coreStats') or {}).get('crossOverrides',0)) for r in good),'noFloorCrossOverrides':sum(int((r.get('noFloorStats') or {}).get('crossOverrides',0)) for r in good)}
    out={'version':'R4_POSITIVE_FLOOR_BRANCH_REDUNDANCY_V3','researchOnly':True,'developmentOnly':True,'comparison':'LITE_CORE vs identical controller without independent positive-floor admission branch; floor remains in lifecycle completion and scoring','rows':rows,'aggregate':agg,'eventsCore':ev_core,'eventsNoFloor':ev_nofloor}
    Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(agg,ensure_ascii=False))
if __name__=='__main__':main()
