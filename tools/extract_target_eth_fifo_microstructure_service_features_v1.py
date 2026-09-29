from __future__ import annotations
import argparse,gzip,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_eth_fifo_microstructure_service_teacher_v1 as a

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--book-db',default='data/wallet_maker_book_inference_eth5m.db');ap.add_argument('--market-offset',type=int,required=True);ap.add_argument('--max-markets',type=int,required=True);ap.add_argument('--output',required=True);x=ap.parse_args();ts=time.time()
 rows,mf,viol,bage=a.build_rows(x.target_db,x.book_db,x.max_markets,x.market_offset)
 out={'version':'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_FEATURE_EXTRACT_V1','marketOffset':x.market_offset,'maxMarkets':x.max_markets,'runtimeSeconds':time.time()-ts,'rows':rows,'marketFirst':{str(k):int(v) for k,v in mf.items()},'invariantViolations':viol,'bookAgeMs':bage,'featureSets':a.SETS,'boundary':['strict-past extraction only','no model fit','current event legs labels/outcomes only','no winner/PnL/future action features']}
 p=Path(x.output);p.parent.mkdir(parents=True,exist_ok=True)
 with gzip.open(p,'wt',encoding='utf-8') as f:json.dump(out,f,separators=(',',':'))
 print(json.dumps({'ok':True,'output':str(p),'rows':len(rows),'markets':len(mf),'runtimeSeconds':out['runtimeSeconds'],'bytes':p.stat().st_size,'invariantViolations':viol,'bookAgeMs':bage},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
