from __future__ import annotations
import argparse,json,sqlite3,zipfile,tempfile,shutil,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_eth_target_style_hft_dev20_bundle_v1 import archive_market
ETH=ROOT/'data/wallet_maker_book_inference_eth5m.db'; TARGET=ROOT/'data/target_wallet_official_v1.db'

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--cutoff',type=int,required=True); ap.add_argument('--limit',type=int,required=True); ap.add_argument('--name',required=True); a=ap.parse_args(); out=ROOT/'data/research/r4_v0/p0_provenance_v1'/a.name
 e=sqlite3.connect(f'file:{ETH}?mode=ro',uri=True); t=sqlite3.connect(f'file:{TARGET}?mode=ro',uri=True)
 mids=[int(r[0]) for r in e.execute('select market_id from maker_book_inference_markets where market_id>? and window_end_ms is not null order by market_id',(a.cutoff,))]; selected=[]
 for mid in mids:
  mr=e.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); end=int(mr[0]) if mr and mr[0] else 0
  if not end: continue
  updates=int(e.execute('select count(*) from maker_book_inference_updates where market_id=?',(mid,)).fetchone()[0]); meta=int(e.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(mid,)).fetchone()[0]); matches=int(e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0]); has=t.execute("select 1 from target_market_results where asset='ETH' and market_id=? limit 1",(mid,)).fetchone() is not None
  if updates>=500 and meta>500 and matches>0 and has:
   selected.append({'marketId':mid,'windowEndMs':end,'updates':updates,'metaRows':meta,'matches':matches,'split':'HOLDOUT','freshAfterMarketId':a.cutoff})
   if len(selected)>=a.limit: break
 if len(selected)<a.limit: raise RuntimeError(f'only {len(selected)} eligible; need {a.limit}')
 out.mkdir(parents=True,exist_ok=True); selection={'version':'ETH_V9_FROZEN_HOLDOUT_SELECTION_V2','cutoffExclusive':a.cutoff,'selection':f'first {a.limit} chronological eligible markets; execution-data eligibility only; no outcome/performance selection','marketIds':[r['marketId'] for r in selected],'first':selected[0]['marketId'],'last':selected[-1]['marketId']}; (out/'selection_manifest.json').write_text(json.dumps(selection,indent=2),encoding='utf-8')
 for r in selected:
  tr=t.execute("select winner,net_pnl_usdt,buy_notional_usdt from target_market_results where asset='ETH' and market_id=?",(r['marketId'],)).fetchone(); r['winner']=str(tr[0]); r['targetPnlScoringOnly']=float(tr[1]); r['targetBuyScoringOnly']=float(tr[2])
 tmp=Path(tempfile.mkdtemp(prefix='eth_v9_holdout_v2_'))
 try:
  (tmp/'tapes').mkdir(parents=True)
  for i,r in enumerate(selected,1):
   archive_market(e,r['marketId'],tmp/'tapes'/f"{r['marketId']}.json.xz")
   if i%10==0 or i==len(selected): print(json.dumps({'archiveProgress':i,'lastMarketId':r['marketId']}),flush=True)
  (tmp/'cohort.json').write_text(json.dumps({'version':'ETH_V9_FROZEN_HOLDOUT_V2','selectionManifest':selection,'rows':selected},indent=2),encoding='utf-8'); (tmp/'trajectory.json').write_text('{}',encoding='utf-8'); z=out/'bundle.zip'
  with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz:
   zz.write(tmp/'cohort.json','cohort.json'); zz.write(tmp/'trajectory.json','trajectory.json')
   for x in sorted((tmp/'tapes').glob('*.json.xz')): zz.write(x,f'tapes/{x.name}')
  meta={'version':'ETH_V9_FROZEN_HOLDOUT_V2','cutoffExclusive':a.cutoff,'markets':len(selected),'first':selected[0]['marketId'],'last':selected[-1]['marketId'],'bundleBytes':z.stat().st_size,'bundleSha256':hashlib.sha256(z.read_bytes()).hexdigest(),'boundary':['candidate remains frozen','chronology-first selection fixed before reading winner/PnL values','winner/PnL scoring only','no tuning on this cohort']}; (out/'cohort.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); print(json.dumps(meta,indent=2),flush=True)
 finally:
  shutil.rmtree(tmp,ignore_errors=True); e.close(); t.close()
if __name__=='__main__': main()
