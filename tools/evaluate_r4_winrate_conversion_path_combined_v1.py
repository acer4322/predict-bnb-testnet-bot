from __future__ import annotations
import argparse,json,sqlite3,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from collections import Counter
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.generate_r4_r3_repair_counterfactual_teacher_v1 import candidate
from tools.validate_r4_r3_repair_counterfactual_teacher_v1 import run_exact,compact,classify
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x.strip()]
 base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'
 settle=root/'target_wallet_official_v1.db'; rows=[]
 for mid in ids:
  rec={'marketId':mid}
  try:
   con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close(); winner=str(rr[0]) if rr else None
   b=r3ctl.run_market(mid,True); cand=candidate(b.get('decisionRows') or [],base.load_public_snapshots(mid)); bs=score(b,winner); bc=compact(b)
   rec.update({'winner':winner,'candidate':cand,'baselineScore':bs,'baselineCompact':bc,'branchClass':'NO_CANDIDATE','exactBranchApplied':False})
   if cand:
    c=run_exact(mid,cand['decisionMs']); cs=score(c,winner); cc=compact(c)
    forced=(cc.get('forced') or []); exact=bool(forced and int(forced[0].get('atMs') or -1)==int(cand['decisionMs']))
    rec.update({'counterfactualScore':cs,'counterfactualCompact':cc,'branchClass':classify(bc,cc),'exactBranchApplied':exact,'deltaPnl':float(cs['pnlUsdt']-bs['pnlUsdt']),'conversion':('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS')})
   else: rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE'
  except Exception as e: rec.update({'error':f'{type(e).__name__}:{e}','branchClass':'ERROR'})
  rows.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'class':rec.get('branchClass'),'error':rec.get('error')}),flush=True)
 cnt=Counter(r.get('conversion') for r in rows if not r.get('error')); rep={'version':'R4_WINRATE_CONVERSION_PATH_COMBINED_V1','researchOnly':True,'actionAuthority':False,'marketCount':len(rows),'conversionCounts':dict(cnt),'rows':rows}
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'conversionCounts':dict(cnt)}))
if __name__=='__main__':main()
