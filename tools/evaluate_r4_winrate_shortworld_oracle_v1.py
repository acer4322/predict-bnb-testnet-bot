from __future__ import annotations
import argparse,json,sqlite3,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
H=[1000,3000,5000]
def pick(rows,target):
 z=[r for r in rows if r.get('decisionMs') is not None]
 if not z:return None
 r=min(z,key=lambda x:abs(int(x['decisionMs'])-int(target)))
 if abs(int(r['decisionMs'])-int(target))>1800:return None
 p=r.get('portfolio') or {}
 return {'decisionMs':int(r['decisionMs']),'dtMs':int(r['decisionMs'])-int(target),'floor':float(p.get('worst_case_floor') or 0),'absNet':float(p.get('combined_abs_net') or 0),'coverage':float(p.get('combined_paired_coverage') or 0),'makerNet':float(p.get('maker_net') or 0),'activeMakerOrders':int(r.get('activeMakerOrders') or 0),'executionChoice':str(r.get('executionChoice') or '')}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x]
 cf.base.STRATEGY_DB=root/'strategy_target_compare_v1.db';cf.base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';cf.base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets';settle=root/'target_wallet_official_v1.db';out=[]
 for mid in ids:
  rec={'marketId':mid}
  try:
   con=sqlite3.connect(settle);w=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(w[0]);b=cf.r3ctl.run_market(mid,True);cand=cf.first_candidate(b.get('decisionRows') or []);bs=score(b,winner);rec.update({'winner':winner,'baselinePnl':bs['pnlUsdt'],'candidate':cand})
   if not cand:rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE';out.append(rec);continue
   c=cf.run_exact(mid,cand['decisionMs']);cs=score(c,winner);rec['counterfactualPnl']=cs['pnlUsdt'];rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS');hz={}
   for h in H:
    br=pick(b.get('decisionRows') or [],int(cand['decisionMs'])+h);cr=pick(c.get('decisionRows') or [],int(cand['decisionMs'])+h);d=None
    if br and cr:d={'floor':cr['floor']-br['floor'],'absNet':cr['absNet']-br['absNet'],'coverage':cr['coverage']-br['coverage'],'makerNet':cr['makerNet']-br['makerNet'],'activeMakerOrders':cr['activeMakerOrders']-br['activeMakerOrders']}
    hz[str(h//1000)]={'baseline':br,'counterfactual':cr,'delta':d}
   rec['horizons']=hz
  except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
  out.append(rec);print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'error':rec.get('error')}),flush=True)
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps({'version':'R4_WINRATE_SHORTWORLD_ORACLE_V1','researchOnly':True,'actionAuthority':False,'rows':out,'boundary':'1/3/5s realized branch consequences are post-action oracle diagnostics only and forbidden as runtime inputs.'},indent=2),encoding='utf-8')
if __name__=='__main__':main()
