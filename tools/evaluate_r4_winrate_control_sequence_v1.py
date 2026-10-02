from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def compact_row(r,t0):
 p=r.get('portfolio') or {}
 return {'dtMs':int(r.get('decisionMs') or 0)-t0,'desired':str(r.get('desiredPortfolioAction') or ''),'exec':str(r.get('executionChoice') or ''),'activeMakerOrders':int(r.get('activeMakerOrders') or 0),'floor':float(p.get('worst_case_floor') or 0),'absNet':float(p.get('combined_abs_net') or 0),'coverage':float(p.get('combined_paired_coverage') or 0),'makerNet':float(p.get('maker_net') or 0)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x]
 cf.base.STRATEGY_DB=root/'strategy_target_compare_v1.db';cf.base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets';settle=root/'target_wallet_official_v1.db';out=[]
 for mid in ids:
  rec={'marketId':mid}
  try:
   con=sqlite3.connect(settle);w=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(w[0]);b=cf.r3ctl.run_market(mid,True);cand=cf.first_candidate(b.get('decisionRows') or []);bs=score(b,winner);rec['candidate']=cand;rec['baselinePnl']=bs['pnlUsdt']
   if not cand:rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE';out.append(rec);continue
   c=cf.run_exact(mid,cand['decisionMs']);cs=score(c,winner);rec['counterfactualPnl']=cs['pnlUsdt'];rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS');t0=int(cand['decisionMs']);rec['baselineSeq']=[compact_row(r,t0) for r in b.get('decisionRows') or [] if 0<=int(r.get('decisionMs') or 0)-t0<=20000];rec['counterfactualSeq']=[compact_row(r,t0) for r in c.get('decisionRows') or [] if 0<=int(r.get('decisionMs') or 0)-t0<=20000]
  except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
  out.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'baseSteps':len(rec.get('baselineSeq') or []),'cfSteps':len(rec.get('counterfactualSeq') or []),'error':rec.get('error')}),flush=True)
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps({'version':'R4_WINRATE_CONTROL_SEQUENCE_V1','researchOnly':True,'rows':out},indent=2),encoding='utf-8')
if __name__=='__main__':main()
