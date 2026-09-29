from __future__ import annotations
import json,sys,time
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hft_native_queue_reactive_chronology_v2 as qv2
BASE=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'
CACHE=ROOT/'data'/'research'/'r4_v0'/'r4_weakliq_cache_v1.csv'
STATE=ROOT/'data'/'research'/'r4_v0'/'r4_weakliq_cache_v1_state.json'

def main():
 contract=json.loads((BASE/'hft_native_unused_chronology_v2_preregistered.json').read_text(encoding='utf-8'))
 chosen={'train':contract['train'],'validation':contract['validation'],'holdout':contract['holdout']}
 order=[(split,int(r['marketId'])) for split in ('train','validation','holdout') for r in chosen[split]]
 collector=json.loads((BASE/'hft_native_unused120_collector_v2.json').read_text(encoding='utf-8'))
 sweep=json.loads((BASE/'hft_native_unused120_bothsides_v2.json').read_text(encoding='utf-8'))
 done=set()
 frames=[]
 if CACHE.exists():
  old=pd.read_csv(CACHE);frames=[old];done=set(int(x) for x in old.market_id.unique())
 start=time.time();processed=0
 for split,mid in order:
  if mid in done: continue
  c={'placementRows':[r for r in collector.get('placementRows',[]) if int(r['market_id'])==mid]}
  s={'rows':[r for r in sweep.get('rows',[]) if int(r['marketId'])==mid]}
  ct=BASE/f'_r4_weakliq_c_{mid}.json';st=BASE/f'_r4_weakliq_s_{mid}.json'
  ct.write_text(json.dumps(c),encoding='utf-8');st.write_text(json.dumps(s),encoding='utf-8')
  try: df=qv2.load_rows(ct.name,st.name)
  finally: ct.unlink(missing_ok=True);st.unlink(missing_ok=True)
  if not df.empty:
   df['split']=split;frames.append(df)
  processed+=1;done.add(mid)
  out=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()
  out.to_csv(CACHE,index=False)
  STATE.write_text(json.dumps({'doneMarkets':len(done),'processedThisRun':processed,'rows':len(out),'lastMarket':mid,'elapsedSec':time.time()-start},indent=2),encoding='utf-8')
  if time.time()-start>22: break
 print(json.dumps({'ok':True,'cache':str(CACHE.relative_to(ROOT)).replace('\\','/'),'doneMarkets':len(done),'processedThisRun':processed,'rows':sum(len(x) for x in frames),'complete':len(done)>=len(order)}))
if __name__=='__main__':main()
