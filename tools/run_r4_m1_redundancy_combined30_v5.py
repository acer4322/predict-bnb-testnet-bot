from __future__ import annotations
import argparse,json,sqlite3,sys,warnings
from pathlib import Path
warnings.filterwarnings('ignore', message='X does not have valid feature names')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_r4_complexity_pruning_guard_ablation_v2 as g
from tools import run_r4_complexity_pruning_m1_ablation_v4 as m
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def bind_data(mod,data:Path):
    mod.v5.base.STRATEGY_DB=data/'strategy_target_compare_v1.db'
    mod.v5.base.mod.BOOK_DB=data/'wallet_maker_book_inference.db'
    mod.v5.base.ex.BOOK_DB=data/'wallet_maker_book_inference.db'
    mod.v5.base.tape_v1.ARCHIVE_DIR=data/'execution_tape_v1/markets'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    data=Path(args.data_root).resolve(); ids=[int(x) for x in json.loads(Path(args.ids_json).read_text())]
    bind_data(g,data);bind_data(m,data)
    settle=data/'target_wallet_official_v1.db'; rows=[]; ev_ref=[];ev_nom1=[]
    for mid in ids:
        rec={'marketId':mid}
        try:
            con=sqlite3.connect(settle); rr=con.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close()
            if not rr or str(rr[0]) not in {'UP','DOWN'}: raise RuntimeError(f'no settlement {mid}')
            winner=str(rr[0]); ref=g.run_overlay(mid,'LITE_NO_POSITIVE_FLOOR',ev_ref); nom1=m.run_overlay(mid,'LITE_NO_M1',ev_nom1)
            A=score(ref,winner);B=score(nom1,winner)
            rec.update({'winner':winner,'reference':A,'noM1':B,'deltaPnl':B['pnlUsdt']-A['pnlUsdt'],'deltaFloor':B['finalFloor']-A['finalFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'referenceStats':ref.get('mgmtStats') or {},'noM1Stats':nom1.get('mgmtStats') or {}})
        except Exception as ex: rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec);print(json.dumps({'marketId':mid,'dPnl':rec.get('deltaPnl'),'dFloor':rec.get('deltaFloor'),'error':rec.get('error')},ensure_ascii=False),flush=True)
    good=[r for r in rows if 'error' not in r]
    agg={'markets':len(good),'errors':len(rows)-len(good),'pnlDifferentMarkets':sum(abs(r['deltaPnl'])>1e-9 for r in good),'floorDifferentMarkets':sum(abs(r['deltaFloor'])>1e-9 for r in good),'absNetDifferentMarkets':sum(abs(r['deltaAbsNet'])>1e-9 for r in good),'totalDeltaPnl':float(sum(r['deltaPnl'] for r in good)),'meanDeltaFloor':float(sum(r['deltaFloor'] for r in good)/len(good)) if good else None,'worstDeltaPnl':min([r['deltaPnl'] for r in good],default=None),'bestDeltaPnl':max([r['deltaPnl'] for r in good],default=None),'improvements':sum(r['deltaPnl']>1e-9 for r in good),'degradations':sum(r['deltaPnl']<-1e-9 for r in good),'ties':sum(abs(r['deltaPnl'])<=1e-9 for r in good),'referenceCrossOverrides':sum(int((r.get('referenceStats') or {}).get('crossOverrides',0)) for r in good),'noM1CrossOverrides':sum(int((r.get('noM1Stats') or {}).get('crossOverrides',0)) for r in good),'referenceOpens':sum(int((r.get('referenceStats') or {}).get('managementObjectiveOpens',0)) for r in good),'noM1Opens':sum(int((r.get('noM1Stats') or {}).get('managementObjectiveOpens',0)) for r in good)}
    out={'version':'R4_M1_REDUNDANCY_COMBINED30_V5','researchOnly':True,'developmentOnly':True,'comparison':'R4-Lite reference (M1 + management latch + small-deficit + completion horizon; no separate positive-floor branch) vs same controller with M1 admission removed','rows':rows,'aggregate':agg,'eventsReference':ev_ref,'eventsNoM1':ev_nom1}
    Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(agg,ensure_ascii=False))
if __name__=='__main__': main()
