from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_r4_complexity_pruning_m1_ablation_v4 as m1
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--reference-cache',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    data=Path(args.data_root).resolve(); ids=[int(x) for x in json.loads(Path(args.ids_json).read_text())];ref=json.loads(Path(args.reference_cache).read_text())
    m1.v5.base.STRATEGY_DB=data/'strategy_target_compare_v1.db';m1.v5.base.mod.BOOK_DB=data/'wallet_maker_book_inference.db';m1.v5.base.ex.BOOK_DB=data/'wallet_maker_book_inference.db';m1.v5.base.tape_v1.ARCHIVE_DIR=data/'execution_tape_v1/markets'
    settle=data/'target_wallet_official_v1.db';rows=[];events=[]
    for mid in ids:
        rec={'marketId':mid}
        try:
            con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close()
            if not rr or str(rr[0]) not in {'UP','DOWN'}:raise RuntimeError(f'no settlement {mid}')
            cand=m1.run_overlay(mid,'LITE_NO_M1',events);B=score(cand,str(rr[0]));A=ref[str(mid)]['score']
            rec.update({'reference':A,'noM1':B,'deltaPnl':B['pnlUsdt']-A['pnlUsdt'],'deltaFloor':B['finalFloor']-A['finalFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'referenceStats':ref[str(mid)].get('stats') or {},'noM1Stats':cand.get('mgmtStats') or {}})
        except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec);print(json.dumps({'marketId':mid,'deltaPnl':rec.get('deltaPnl'),'error':rec.get('error')},ensure_ascii=False),flush=True)
    good=[r for r in rows if 'error' not in r]
    agg={'markets':len(good),'errors':len(rows)-len(good),'totalDeltaPnl':sum(r['deltaPnl'] for r in good),'improvements':sum(r['deltaPnl']>1e-9 for r in good),'degradations':sum(r['deltaPnl']<-1e-9 for r in good),'ties':sum(abs(r['deltaPnl'])<=1e-9 for r in good),'worstDeltaPnl':min([r['deltaPnl'] for r in good],default=None),'bestDeltaPnl':max([r['deltaPnl'] for r in good],default=None),'meanDeltaFloor':sum(r['deltaFloor'] for r in good)/len(good) if good else None,'referenceCrossOverrides':sum(int((r.get('referenceStats') or {}).get('crossOverrides',0)) for r in good),'noM1CrossOverrides':sum(int((r.get('noM1Stats') or {}).get('crossOverrides',0)) for r in good)}
    Path(args.output).write_text(json.dumps({'version':'R4_M1_REDUNDANCY_COMPARE_V4','researchOnly':True,'developmentOnly':True,'rows':rows,'aggregate':agg,'events':events},indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(agg,ensure_ascii=False))
if __name__=='__main__':main()
