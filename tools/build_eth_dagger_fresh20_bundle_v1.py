from __future__ import annotations
import json,sqlite3,zipfile,tempfile,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.build_eth_target_style_hft_dev20_bundle_v1 import archive_market
ETH=ROOT/'data/wallet_maker_book_inference_eth5m.db';TARGET=ROOT/'data/target_wallet_official_v1.db'
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_dagger60_v1/bundle.zip'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_dagger_fresh20_v1'
CUTOFF=1815254

def main():
 tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_fresh20_'))
 try:
  zipfile.ZipFile(BASE).extractall(tmp/'base')
  base_cohort=json.load(open(tmp/'base'/'cohort.json',encoding='utf-8'))['rows'];train=[dict(r) for r in base_cohort if r['split']=='TRAIN40']
  base_traj=json.load(open(tmp/'base'/'trajectory.json',encoding='utf-8'))
  e=sqlite3.connect(f'file:{ETH}?mode=ro',uri=True);t=sqlite3.connect(f'file:{TARGET}?mode=ro',uri=True)
  mids=[int(r[0]) for r in e.execute('select market_id from maker_book_inference_markets where market_id>? and window_end_ms is not null order by market_id',(CUTOFF,))]
  fresh=[]
  for mid in mids:
   mr=e.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone();end=int(mr[0]) if mr and mr[0] else 0
   if not end:continue
   updates=e.execute('select count(*) from maker_book_inference_updates where market_id=?',(mid,)).fetchone()[0]
   meta=e.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(mid,)).fetchone()[0]
   matches=e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0]
   tr=t.execute("select winner,net_pnl_usdt,buy_notional_usdt from target_market_results where asset='ETH' and market_id=?",(mid,)).fetchone()
   if updates>=500 and meta>500 and matches>0 and tr:
    fresh.append({'marketId':mid,'windowEndMs':end,'winner':str(tr[0]),'targetPnl':float(tr[1]),'targetBuy':float(tr[2]),'split':'TEST20','freshAfterMarketId':CUTOFF,'updates':int(updates),'metaRows':int(meta),'matches':int(matches)})
    if len(fresh)>=20:break
  if len(fresh)<20:raise RuntimeError(f'only {len(fresh)} fresh eligible markets')
  for r in train:r['split']='TRAIN40'
  rows=train+fresh
  outtmp=tmp/'out';(outtmp/'tapes').mkdir(parents=True)
  # reuse exact TRAIN40 tapes and trajectories from frozen original bundle
  for r in train:
   src=tmp/'base'/'tapes'/f"{r['marketId']}.json.xz";shutil.copy2(src,outtmp/'tapes'/src.name)
  # archive fresh TEST20 directly from current ETH public execution DB
  for i,r in enumerate(fresh,1):
   archive_market(e,r['marketId'],outtmp/'tapes'/f"{r['marketId']}.json.xz")
   if i%5==0:print(json.dumps({'freshArchiveProgress':i,'lastMarketId':r['marketId']}),flush=True)
  traj={str(r['marketId']):base_traj.get(str(r['marketId']),[]) for r in train}
  (outtmp/'cohort.json').write_text(json.dumps({'version':'ETH_DAGGER_FRESH20_V1','cutoffExclusive':CUTOFF,'selection':'first 20 chronological eligible after cutoff; no outcome-based selection','rows':rows},indent=2),encoding='utf-8')
  (outtmp/'trajectory.json').write_text(json.dumps(traj),encoding='utf-8')
  OUT.mkdir(parents=True,exist_ok=True);z=OUT/'bundle.zip'
  with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz:
   zz.write(outtmp/'cohort.json','cohort.json');zz.write(outtmp/'trajectory.json','trajectory.json')
   for x in (outtmp/'tapes').glob('*.json.xz'):zz.write(x,f'tapes/{x.name}')
  meta={'version':'ETH_DAGGER_FRESH20_V1','cutoffExclusive':CUTOFF,'train40':[r['marketId'] for r in train],'fresh20':[r['marketId'] for r in fresh],'firstFresh':fresh[0]['marketId'],'lastFresh':fresh[-1]['marketId'],'bundleBytes':z.stat().st_size,'boundary':['candidate frozen before fresh selection','fresh test markets strictly after prior TEST20 max marketId','first 20 chronological eligible after cutoff; no outcome/performance selection','TRAIN40 unchanged from eth_dagger60_v1','Target fresh winner/PnL only for post-replay scoring; no fresh Target trajectory provided to runtime']}
  (OUT/'cohort.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,indent=2),flush=True);e.close();t.close()
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
