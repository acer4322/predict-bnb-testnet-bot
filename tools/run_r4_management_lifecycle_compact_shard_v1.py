from __future__ import annotations
import argparse,json,os,shutil,sys,time
from pathlib import Path
ROOT=Path.cwd().resolve()
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.predict_wallet_maker_book_inference_collector_v2_1_impl import MakerBookConsumableLifecycleCollector

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--ids-json',required=True);a=ap.parse_args()
 db=(ROOT/a.db).resolve(); ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text())['backfillMarkets']]
 dummy=db.with_suffix('.worker_target_dummy.db');c=MakerBookConsumableLifecycleCollector(db,dummy)
 rows=[];t0=time.time()
 try:
  for i,m in enumerate(ids,1):
   ok=bool(c._reconcile_market(m));n=int(c.db.execute('select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=?',(m,)).fetchone()[0]);hc=int(c.db.execute('''select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75''',(m,)).fetchone()[0]);rows.append({'marketId':m,'ok':ok,'lifecycles':n,'highConfidence':hc})
   if i%5==0:print(json.dumps({'progress':i,'markets':len(ids),'marketId':m,'lifecycles':n,'highConfidence':hc}),flush=True)
 finally:c.stop()
 outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR') or (ROOT/'data/research/lan_worker_results_local'));outdir.mkdir(parents=True,exist_ok=True)
 outdb=outdir/'compact_result.db';shutil.copy2(db,outdb)
 rep={'version':'R4_MANAGEMENT_LIFECYCLE_COMPACT_SHARD_RESULT_V1','researchOnly':True,'markets':len(ids),'marketsWithLifecycle':sum(r['lifecycles']>0 for r in rows),'marketsWithHighConfidence':sum(r['highConfidence']>0 for r in rows),'totalLifecycles':sum(r['lifecycles'] for r in rows),'totalHighConfidence':sum(r['highConfidence'] for r in rows),'elapsedSec':time.time()-t0,'marketRows':rows,'resultDb':'compact_result.db'}
 (outdir/'report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:rep[k] for k in ['markets','marketsWithLifecycle','marketsWithHighConfidence','totalLifecycles','totalHighConfidence','elapsedSec']},indent=2))
if __name__=='__main__':main()
