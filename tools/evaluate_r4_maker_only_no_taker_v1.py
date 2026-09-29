from __future__ import annotations
import argparse,json,sqlite3,sys
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

def compact(r):
    x=r['studentRollout'];p=x['finalPortfolio']
    return {
      'makerFillEvents':int(x.get('makerFillEvents') or 0),'makerFilledShares':float(x.get('makerFilledShares') or 0),
      'takerFills':int(x.get('takerFills') or 0),'takerFilledShares':float(x.get('takerFilledShares') or 0),
      'blockedTakerAttempts':sum(1 for z in (r.get('takerAttempts') or []) if z.get('result')=='BLOCKED_NO_TAKER_RESEARCH'),
      'finalAbsNet':float(p.get('combined_abs_net') or 0),'finalFloor':float(p.get('worst_case_floor') or 0),
      'makerPlacements':int(x.get('makerPlacements') or 0)
    }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 root=Path(a.data_root).resolve();configure(root);ids=[int(x) for x in a.ids.split(',') if x.strip()];settle=root/'target_wallet_official_v1.db';rows=[]
 for mid in ids:
  rec={'marketId':mid}
  try:
   con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
   if not winner: raise RuntimeError('winner_missing')
   b=r3ctl.run_market(mid,True,disable_taker=False);n=r3ctl.run_market(mid,True,disable_taker=True)
   bs=score(b,winner);ns=score(n,winner)
   rec.update({'winner':winner,'baselineScore':bs,'noTakerScore':ns,'baseline':compact(b),'noTaker':compact(n),'deltaPnl':float(ns['pnlUsdt']-bs['pnlUsdt']),'conversion':('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if ns['pnlUsdt']>0 else 'LOSS')})
  except Exception as e: rec['error']=f'{type(e).__name__}:{e}'
  rows.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'deltaPnl':rec.get('deltaPnl'),'error':rec.get('error')}),flush=True)
 ok=[r for r in rows if not r.get('error')];conv=Counter(r['conversion'] for r in ok);bw=sum(r['baselineScore']['pnlUsdt']>0 for r in ok);nw=sum(r['noTakerScore']['pnlUsdt']>0 for r in ok)
 rep={'version':'R4_MAKER_ONLY_NO_TAKER_V1','researchOnly':True,'actionAuthority':False,'dreamFillAllowed':False,'semantics':'Frozen R3+R3.1 realistic-HFT replay with identical Maker/controller path except all Taker execution attempts fail closed before submission. Winner used post-episode scoring only.','summary':{'n':len(ok),'baselineWins':bw,'noTakerWins':nw,'baselineWinRate':bw/len(ok) if ok else None,'noTakerWinRate':nw/len(ok) if ok else None,'baselinePnl':sum(r['baselineScore']['pnlUsdt'] for r in ok),'noTakerPnl':sum(r['noTakerScore']['pnlUsdt'] for r in ok),'deltaPnl':sum(r['deltaPnl'] for r in ok),'conversionCounts':dict(conv),'baselineTakerShares':sum(r['baseline']['takerFilledShares'] for r in ok),'noTakerShares':sum(r['noTaker']['takerFilledShares'] for r in ok),'blockedTakerAttempts':sum(r['noTaker']['blockedTakerAttempts'] for r in ok),'baselineMakerShares':sum(r['baseline']['makerFilledShares'] for r in ok),'noTakerMakerShares':sum(r['noTaker']['makerFilledShares'] for r in ok)},'rows':rows}
 p=(Path(__import__('os').environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(rep['summary'],indent=2))
if __name__=='__main__':main()
