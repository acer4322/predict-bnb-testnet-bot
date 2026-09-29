from __future__ import annotations
import json,sqlite3,zipfile,tempfile,shutil,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.build_eth_target_style_hft_dev20_bundle_v1 import archive_market
ETH=ROOT/'data/wallet_maker_book_inference_eth5m.db'
TARGET=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_v9_frozen_holdout100_v1'
CUTOFF=1823545
LIMIT=100

def main():
    e=sqlite3.connect(f'file:{ETH}?mode=ro',uri=True)
    t=sqlite3.connect(f'file:{TARGET}?mode=ro',uri=True)
    mids=[int(r[0]) for r in e.execute('select market_id from maker_book_inference_markets where market_id>? and window_end_ms is not null order by market_id',(CUTOFF,))]
    selected=[]
    for mid in mids:
        mr=e.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); end=int(mr[0]) if mr and mr[0] else 0
        if not end: continue
        updates=int(e.execute('select count(*) from maker_book_inference_updates where market_id=?',(mid,)).fetchone()[0])
        meta=int(e.execute('select count(*) from maker_execution_orderbook_meta_v1 where market_id=?',(mid,)).fetchone()[0])
        matches=int(e.execute('select count(*) from maker_execution_matches_v1 where market_id=?',(mid,)).fetchone()[0])
        has_result=t.execute("select 1 from target_market_results where asset='ETH' and market_id=? limit 1",(mid,)).fetchone() is not None
        if updates>=500 and meta>500 and matches>0 and has_result:
            selected.append({'marketId':mid,'windowEndMs':end,'updates':updates,'metaRows':meta,'matches':matches,'split':'HOLDOUT100','freshAfterMarketId':CUTOFF})
            if len(selected)>=LIMIT: break
    if len(selected)<LIMIT: raise RuntimeError(f'only {len(selected)} eligible; need {LIMIT}')
    OUT.mkdir(parents=True,exist_ok=True)
    selection={'version':'ETH_V9_FROZEN_HOLDOUT100_SELECTION_V1','cutoffExclusive':CUTOFF,'selection':'first 100 chronological eligible markets; execution-data eligibility only; no outcome/performance selection','marketIds':[r['marketId'] for r in selected],'first':selected[0]['marketId'],'last':selected[-1]['marketId']}
    (OUT/'selection_manifest.json').write_text(json.dumps(selection,indent=2),encoding='utf-8')
    # Only after chronology-first selection is frozen, attach winner/PnL for post-replay scoring.
    for r in selected:
        tr=t.execute("select winner,net_pnl_usdt,buy_notional_usdt from target_market_results where asset='ETH' and market_id=?",(r['marketId'],)).fetchone()
        r['winner']=str(tr[0]); r['targetPnlScoringOnly']=float(tr[1]); r['targetBuyScoringOnly']=float(tr[2])
    tmp=Path(tempfile.mkdtemp(prefix='eth_v9_holdout100_'))
    try:
        (tmp/'tapes').mkdir(parents=True)
        for i,r in enumerate(selected,1):
            archive_market(e,r['marketId'],tmp/'tapes'/f"{r['marketId']}.json.xz")
            if i%10==0 or i==len(selected): print(json.dumps({'archiveProgress':i,'lastMarketId':r['marketId']}),flush=True)
        (tmp/'cohort.json').write_text(json.dumps({'version':'ETH_V9_FROZEN_HOLDOUT100_V1','cutoffExclusive':CUTOFF,'selectionManifest':selection,'rows':selected},indent=2),encoding='utf-8')
        (tmp/'trajectory.json').write_text('{}',encoding='utf-8')
        z=OUT/'bundle.zip'
        with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as zz:
            zz.write(tmp/'cohort.json','cohort.json'); zz.write(tmp/'trajectory.json','trajectory.json')
            for x in sorted((tmp/'tapes').glob('*.json.xz')): zz.write(x,f'tapes/{x.name}')
        meta={'version':'ETH_V9_FROZEN_HOLDOUT100_V1','cutoffExclusive':CUTOFF,'markets':len(selected),'first':selected[0]['marketId'],'last':selected[-1]['marketId'],'bundleBytes':z.stat().st_size,'bundleSha256':hashlib.sha256(z.read_bytes()).hexdigest(),'boundary':['candidate frozen before selection','first 100 chronological eligible after cutoff','selection fixed before reading winner/PnL values','winner/PnL scoring only; runtime policy cannot access Target trajectory','no tuning on holdout100']}
        (OUT/'cohort.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
        print(json.dumps(meta,indent=2),flush=True)
    finally:
        shutil.rmtree(tmp,ignore_errors=True); e.close(); t.close()
if __name__=='__main__': main()
