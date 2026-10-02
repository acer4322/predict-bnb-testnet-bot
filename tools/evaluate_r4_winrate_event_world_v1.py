from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

def milestones(rep,t0,cand):
 maker=[x for x in rep.get('makerFillEvents') or [] if int(x.get('atMs') or 0)>=t0]
 taker=[x for x in rep.get('takerEvents') or [] if int(x.get('atMs') or 0)>=t0]
 ev=[]
 for x in maker:ev.append((int(x.get('atMs') or 0),'MAKER_FILL',str(x.get('side') or ''),float(x.get('deltaShares') or 0)))
 for x in taker:ev.append((int(x.get('atMs') or 0),'TAKER_FILL',str(x.get('side') or ''),float(x.get('shares') or 0)))
 ev.sort()
 rows=[r for r in rep.get('decisionRows') or [] if int(r.get('decisionMs') or 0)>=t0]
 floor_cross=None;abs_reduce=None
 base_abs=float(((cand or {}).get('portfolio') or {}).get('combined_abs_net') or ((cand or {}).get('portfolio') or {}).get('maker_abs_net') or 0)
 for r in rows:
  p=r.get('portfolio') or {};dt=int(r.get('decisionMs') or 0)-t0
  if floor_cross is None and float(p.get('worst_case_floor') or 0)>0:floor_cross={'delayMs':dt,'floor':float(p.get('worst_case_floor') or 0)}
  if abs_reduce is None and float(p.get('combined_abs_net') or 0)<base_abs-1e-9:abs_reduce={'delayMs':dt,'absNet':float(p.get('combined_abs_net') or 0)}
 return {'firstEconomicEvent':({'delayMs':ev[0][0]-t0,'type':ev[0][1],'side':ev[0][2],'shares':ev[0][3]} if ev else None),'firstMakerFill':({'delayMs':maker[0]['atMs']-t0,'side':maker[0].get('side'),'shares':maker[0].get('deltaShares')} if maker else None),'firstTakerFill':({'delayMs':taker[0]['atMs']-t0,'side':taker[0].get('side'),'shares':taker[0].get('shares')} if taker else None),'floorCrossPositive':floor_cross,'absNetReduction':abs_reduce,'makerFillCount60s':sum(int(x.get('atMs') or 0)<=t0+60000 for x in maker),'takerFillCount60s':sum(int(x.get('atMs') or 0)<=t0+60000 for x in taker)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x]
 cf.base.STRATEGY_DB=root/'strategy_target_compare_v1.db';cf.base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets';settle=root/'target_wallet_official_v1.db';out=[]
 for mid in ids:
  rec={'marketId':mid}
  try:
   con=sqlite3.connect(settle);w=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(w[0]);b=cf.r3ctl.run_market(mid,True);cand=cf.first_candidate(b.get('decisionRows') or []);bs=score(b,winner);rec['baselinePnl']=bs['pnlUsdt'];rec['candidate']=cand
   if not cand:rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE';out.append(rec);continue
   c=cf.run_exact(mid,cand['decisionMs']);cs=score(c,winner);rec['counterfactualPnl']=cs['pnlUsdt'];rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS');t0=int(cand['decisionMs']);rec['baselineEvents']=milestones(b,t0,cand);rec['counterfactualEvents']=milestones(c,t0,cand)
  except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
  out.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'baseNext':(rec.get('baselineEvents') or {}).get('firstEconomicEvent'),'cfNext':(rec.get('counterfactualEvents') or {}).get('firstEconomicEvent'),'error':rec.get('error')}),flush=True)
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps({'version':'R4_WINRATE_EVENT_WORLD_V1','researchOnly':True,'rows':out},indent=2),encoding='utf-8')
if __name__=='__main__':main()
