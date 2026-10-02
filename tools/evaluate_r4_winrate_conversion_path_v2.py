from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=="tools" else Path(r"C:\BTC5M-worker")
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from collections import Counter
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x]
 cf.base.STRATEGY_DB=root/'strategy_target_compare_v1.db';cf.base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'
 settle=root/'target_wallet_official_v1.db';rows=[]
 for mid in ids:
  con=sqlite3.connect(settle);r=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(r[0]) if r else None;rec={'marketId':mid,'winner':winner}
  try:
   b=cf.r3ctl.run_market(mid,True);cand=cf.first_candidate(b.get('decisionRows') or []);bs=score(b,winner);rec['baseline']=bs;rec['candidate']=cand
   if cand:
    c=cf.run_exact(mid,cand['decisionMs']);cs=score(c,winner);forced=c.get('forcedRepairWakeV1') or [];rec['counterfactual']=cs;rec['deltaPnl']=cs['pnlUsdt']-bs['pnlUsdt'];rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS');rec['exactBranchApplied']=bool(forced and int(forced[0].get('atMs') or -1)==int(cand['decisionMs']));rec['pathState']=(forced[0].get('activeOrderPathState') if forced else None)
    bc=cf.compact(b);cc=cf.compact(c);rec['branchClass']=cf.classify(bc,cc);rec['economicDelta']={'finalFloor':cc['finalFloor']-bc['finalFloor'],'finalAbsNet':cc['finalAbsNet']-bc['finalAbsNet'],'finalCoverage':cc['finalCoverage']-bc['finalCoverage'],'makerFilledShares':cc['makerFilledShares']-bc['makerFilledShares'],'takerFilledShares':cc['takerFilledShares']-bc['takerFilledShares'],'makerCostUsdt':cc['makerCostUsdt']-bc['makerCostUsdt'],'takerCostUsdt':cc['takerCostUsdt']-bc['takerCostUsdt'],'takerFeesUsdt':cc['takerFeesUsdt']-bc['takerFeesUsdt']}
   else: rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE';rec['exactBranchApplied']=False;rec['pathState']=None
  except Exception as ex: rec['error']=f'{type(ex).__name__}:{ex}'
  rows.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'deltaPnl':rec.get('deltaPnl'),'exact':rec.get('exactBranchApplied'),'error':rec.get('error')}),flush=True)
 good=[r for r in rows if 'error' not in r];cnt=Counter(r.get('conversion') for r in good);rep={'version':'R4_ADAPTIVE_WINRATE_CONVERSION_PATH_V2','researchOnly':True,'actionAuthority':False,'marketCount':len(good),'conversionCounts':dict(cnt),'rows':rows,'boundary':'Settlement/conversion labels are post-episode scoring only. candidate and pathState are exact strict-past seam observations.'};Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'conversionCounts':dict(cnt)},indent=2))
if __name__=='__main__':main()
