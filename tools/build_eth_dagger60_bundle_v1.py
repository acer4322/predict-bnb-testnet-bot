from __future__ import annotations
import json,sqlite3,zipfile,tempfile,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.build_eth_target_style_hft_dev20_bundle_v1 import archive_market
ETH=ROOT/'data/wallet_maker_book_inference_eth5m.db';TARGET=ROOT/'data/target_wallet_official_v1.db';PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_dagger60_v1'
BLOCK={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}
def main():
 p=json.load(open(PLAC,encoding='utf-8'))['rows'];mids=sorted({int(r['marketId']) for r in p if r.get('highConfidencePlacement') and int(r['marketId']) not in BLOCK});e=sqlite3.connect(ETH);t=sqlite3.connect(TARGET);eligible=[]
 for mid in mids:
  mr=e.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone();
  if not mr or not mr[0]:continue
  end=int(mr[0]);early=e.execute("select count(*) from maker_book_inference_wallet_events where market_id=? and role='MAKER' and quote_type='BID' and event_ms<?",(mid,end-180000)).fetchone()[0]
  meta=e.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(mid,)).fetchone()[0];matches=e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0];tr=t.execute("select winner,net_pnl_usdt,buy_notional_usdt from target_market_results where asset='ETH' and market_id=?",(mid,)).fetchone()
  if early>0 and meta>500 and matches>0 and tr:eligible.append({'marketId':mid,'windowEndMs':end,'earlyMakerEvents':int(early),'winner':str(tr[0]),'targetPnl':float(tr[1]),'targetBuy':float(tr[2])})
 if len(eligible)<60:raise RuntimeError(f'only {len(eligible)} eligible')
 # take latest 60 within this historical pilot, then chronology split 40/20
 rows=eligible[-60:]
 for i,r in enumerate(rows):r['split']='TRAIN40' if i<40 else 'TEST20'
 traj={}
 for r in rows:
  mid=r['marketId'];ev=e.execute("select event_ms,side,shares,price,source_leg_id from maker_book_inference_wallet_events where market_id=? and role='MAKER' and quote_type='BID' order by event_ms,source_leg_id",(mid,)).fetchall();traj[str(mid)]=[{'t':int(a),'side':str(b),'shares':float(c),'price':float(d),'id':x} for a,b,c,d,x in ev]
 tmp=Path(tempfile.mkdtemp(prefix='eth_dagger60_'))
 try:
  (tmp/'tapes').mkdir();(tmp/'cohort.json').write_text(json.dumps({'version':'ETH_DAGGER60_V1','rows':rows},indent=2),encoding='utf-8');(tmp/'trajectory.json').write_text(json.dumps(traj),encoding='utf-8')
  for i,r in enumerate(rows,1):archive_market(e,r['marketId'],tmp/'tapes'/f"{r['marketId']}.json.xz");
  OUT.mkdir(parents=True,exist_ok=True);z=OUT/'bundle.zip'
  with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz:
   zz.write(tmp/'cohort.json','cohort.json');zz.write(tmp/'trajectory.json','trajectory.json')
   for x in (tmp/'tapes').glob('*.json.xz'):zz.write(x,f'tapes/{x.name}')
  (OUT/'cohort.json').write_text(json.dumps({'version':'ETH_DAGGER60_V1','rows':rows},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'eligible':len(eligible),'selected':len(rows),'train':40,'test':20,'first':rows[0]['marketId'],'last':rows[-1]['marketId'],'bundleBytes':z.stat().st_size},indent=2))
 finally:shutil.rmtree(tmp,ignore_errors=True);e.close();t.close()
if __name__=='__main__':main()
