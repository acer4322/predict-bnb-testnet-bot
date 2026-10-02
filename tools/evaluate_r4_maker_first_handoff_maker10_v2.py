from __future__ import annotations
import argparse,json,sqlite3,sys,os
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def configure(root:Path):
 base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';ex.BOOK_DB=root/'wallet_maker_book_inference.db';tape.ARCHIVE_DIR=root/'execution_tape_v1/markets'

def compact(rep,audit):
 s=rep['studentRollout'];p=s['finalPortfolio'];ev=rep.get('makerFirstHandoffEvents') or [];ta=rep.get('takerAttempts') or []
 return {'makerFilledShares':float(s.get('makerFilledShares') or 0),'takerFilledShares':float(s.get('takerFilledShares') or 0),'makerFillEvents':int(s.get('makerFillEvents') or 0),'takerFills':int(s.get('takerFills') or 0),'finalFloor':float(p.get('worst_case_floor') or 0),'finalAbsNet':float(p.get('combined_abs_net') or 0),'makerFirstEvents':len(ev),'progressResize':sum(x.get('result')=='PROGRESS_RESIZE_HANDOFF' for x in ev),'resolvedContinue':sum(x.get('result') in {'RESOLVED_CONTINUE','RESOLVED_DURING_CANCEL'} for x in ev),'sideChanged':sum(x.get('result')=='REPAIR_SIDE_CHANGED_REDECIDE' for x in ev),'handoffTaker':sum('HANDOFF_TAKER' in str(x.get('result')) or x.get('result')=='PROGRESS_RESIZE_HANDOFF' for x in ev),'takerSizingMode':audit.get('takerSizingMode'),'takerRequestedQty':[float(x.get('requestedShares') or 0) for x in ta if x.get('requestedShares') is not None],'takerRequestedMax':max([float(x.get('requestedShares') or 0) for x in ta if x.get('requestedShares') is not None],default=0.0)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();configure(root);ids=[int(x) for x in a.ids.split(',') if x.strip()];settle=root/'target_wallet_official_v1.db';rows=[]
 for mid in ids:
  rec={'marketId':mid,'branches':{}}
  try:
   con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
   if winner not in {'UP','DOWN'}:raise RuntimeError('winner_missing')
   for name,mode,wait in [('BASELINE','NONE',2200),('MAKER_FIRST_2200','REPRICE_1T',2200),('MAKER_FIRST_5000','REPRICE_1T',5000)]:
    rep,audit=mk10.run_market(mid,maker_first_repair_mode=mode,maker_first_wait_ms=wait);rec['branches'][name]={'score':score(rep,winner),'compact':compact(rep,audit),'events':rep.get('makerFirstHandoffEvents') or [],'adapterAudit':audit}
   bp=rec['branches']['BASELINE']['score']['pnlUsdt'];rec['winner']=winner
   for name in ('MAKER_FIRST_2200','MAKER_FIRST_5000'):
    cp=rec['branches'][name]['score']['pnlUsdt'];rec['branches'][name]['deltaPnl']=float(cp-bp);rec['branches'][name]['conversion']=('WIN' if bp>0 else 'LOSS')+'->'+('WIN' if cp>0 else 'LOSS')
  except Exception as e:rec['error']=f'{type(e).__name__}:{e}'
  rows.append(rec);print(json.dumps({'marketId':mid,'error':rec.get('error'),'2200':rec.get('branches',{}).get('MAKER_FIRST_2200',{}).get('conversion'),'5000':rec.get('branches',{}).get('MAKER_FIRST_5000',{}).get('conversion')},ensure_ascii=False),flush=True)
 ok=[r for r in rows if not r.get('error')];summary={}
 for name in ('BASELINE','MAKER_FIRST_2200','MAKER_FIRST_5000'):
  z=[r['branches'][name] for r in ok];summary[name]={'n':len(z),'wins':sum(x['score']['pnlUsdt']>0 for x in z),'pnl':sum(x['score']['pnlUsdt'] for x in z),'makerShares':sum(x['compact']['makerFilledShares'] for x in z),'takerShares':sum(x['compact']['takerFilledShares'] for x in z),'makerFirstEvents':sum(x['compact']['makerFirstEvents'] for x in z),'progressResize':sum(x['compact']['progressResize'] for x in z),'resolvedContinue':sum(x['compact']['resolvedContinue'] for x in z),'sideChanged':sum(x['compact']['sideChanged'] for x in z),'takerSizingModes':dict(Counter(x['compact']['takerSizingMode'] for x in z))}
 for name in ('MAKER_FIRST_2200','MAKER_FIRST_5000'):
  summary[name]['conversions']=dict(Counter(r['branches'][name]['conversion'] for r in ok));summary[name]['deltaPnl']=sum(r['branches'][name]['deltaPnl'] for r in ok);summary[name]['deltaTakerShares']=summary[name]['takerShares']-summary['BASELINE']['takerShares'];summary[name]['deltaMakerShares']=summary[name]['makerShares']-summary['BASELINE']['makerShares']
 rep={'version':'R4_MAKER_FIRST_HANDOFF_MAKER10_V2','researchOnly':True,'actionAuthority':False,'dreamFillAllowed':False,'baseline':'R3+R3.1 Maker10 + r3_rawq dynamic Taker','semantics':'At stale/far structural Repair Taker seam only: terminal-safe reprice genuine weak-side Maker remainder to 1T, allow bounded progress window, reconcile latest inventory; if repair remains on same side, native R3 raw-q Taker continues but is capped only by latest residual obligation; if resolved/side changes, old Taker intent is invalidated. Young/near-touch carriers are not churned and do not suppress Taker.','summary':summary,'rows':rows,'guards':['realistic HFT','Maker child max10 current adapter','r3_rawq dynamic Taker','no artificial 18-share Taker cap','latest residual anti-overshoot only','strict-past runtime state','no new responsibility','cancel terminal before replacement','fill during cancel reconciled','no live mutation']}
 p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.out=='AUTO' else Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
