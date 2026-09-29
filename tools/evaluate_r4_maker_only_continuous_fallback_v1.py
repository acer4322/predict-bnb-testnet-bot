from __future__ import annotations
import argparse,json,sqlite3,sys,os
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
MODES=('NONE','REPRICE_1T','REPRICE_3T')

def configure(root:Path):
 base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'

def compact(r):
 s=r['studentRollout'];p=s['finalPortfolio'];fb=r.get('makerFallbackEvents') or []
 return {'makerFillEvents':int(s.get('makerFillEvents') or 0),'makerFilledShares':float(s.get('makerFilledShares') or 0),'takerFilledShares':float(s.get('takerFilledShares') or 0),'makerPlacements':int(s.get('makerPlacements') or 0),'blockedTakerAttempts':sum(1 for z in (r.get('takerAttempts') or []) if z.get('result')=='BLOCKED_NO_TAKER_RESEARCH'),'fallbackEvents':len(fb),'fallbackReplaced':sum(1 for z in fb if z.get('result')=='REPLACED'),'fallbackNoCarrier':sum(1 for z in fb if z.get('result')=='NO_ACTIVE_REPAIR_CARRIER'),'finalAbsNet':float(p.get('combined_abs_net') or 0),'finalFloor':float(p.get('worst_case_floor') or 0)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();configure(root);ids=[int(x) for x in a.ids.split(',') if x.strip()];settle=root/'target_wallet_official_v1.db';rows=[]
 for mid in ids:
  rec={'marketId':mid,'branches':{}}
  try:
   con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
   if not winner: raise RuntimeError('winner_missing')
   for mode in MODES:
    r=r3ctl.run_market(mid,True,disable_taker=True,no_taker_fallback_mode=mode);rec['branches'][mode]={'score':score(r,winner),'compact':compact(r)}
   b=rec['branches']['NONE']['score']['pnlUsdt'];rec['winner']=winner
   for mode in ('REPRICE_1T','REPRICE_3T'):
    p=rec['branches'][mode]['score']['pnlUsdt'];rec['branches'][mode]['deltaPnl']=float(p-b);rec['branches'][mode]['conversion']=('WIN' if b>0 else 'LOSS')+'->'+('WIN' if p>0 else 'LOSS')
  except Exception as e: rec['error']=f'{type(e).__name__}:{e}'
  rows.append(rec);print(json.dumps({'marketId':mid,'error':rec.get('error'),'conv1':rec.get('branches',{}).get('REPRICE_1T',{}).get('conversion'),'conv3':rec.get('branches',{}).get('REPRICE_3T',{}).get('conversion')}),flush=True)
 ok=[r for r in rows if not r.get('error')];summary={}
 for mode in MODES:
  z=[r['branches'][mode] for r in ok];wins=sum(x['score']['pnlUsdt']>0 for x in z);summary[mode]={'n':len(z),'wins':wins,'winRate':wins/len(z) if z else None,'pnl':sum(x['score']['pnlUsdt'] for x in z),'makerShares':sum(x['compact']['makerFilledShares'] for x in z),'takerShares':sum(x['compact']['takerFilledShares'] for x in z),'fallbackEvents':sum(x['compact']['fallbackEvents'] for x in z),'fallbackReplaced':sum(x['compact']['fallbackReplaced'] for x in z)}
 for mode in ('REPRICE_1T','REPRICE_3T'):summary[mode]['conversions']=dict(Counter(r['branches'][mode]['conversion'] for r in ok));summary[mode]['deltaPnl']=sum(r['branches'][mode]['deltaPnl'] for r in ok)
 rep={'version':'R4_MAKER_ONLY_CONTINUOUS_FALLBACK_V1','researchOnly':True,'actionAuthority':False,'dreamFillAllowed':False,'semantics':'All Taker submissions are blocked. At each structural Taker-repair attempt, optionally terminal-safe cancel/reconcile/reprice the least-competitive existing same-side Maker remainder. No new quantity is created; no-carrier cases WAIT. Winner is post-episode scoring only.','summary':summary,'rows':rows}
 p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
