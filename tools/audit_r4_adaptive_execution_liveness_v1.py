from __future__ import annotations
import argparse,json,math,sys
from collections import defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9
TH=[1,2,3,5,10]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'
 rep,audit=mk10.run_market(int(a.market_id)); rows=[];seen=set()
 for r0 in rep.get('orderStateRows') or []:
  if str(r0.get('context') or '')!='POST_DECISION_ACTIVE':continue
  if str(r0.get('hftStatus') or '') not in {'NEW','PARTIALLY_FILLED'}:continue
  key=(str(r0.get('orderId')),int(r0.get('checkpointMs') or 0))
  if key in seen:continue
  seen.add(key);r=dict(r0);p=r.get('portfolio') if isinstance(r.get('portfolio'),dict) else {};net=float(p.get('combined_net') or 0.0);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
  r['net']=net;r['gap']=abs(net);r['weak']=weak;r['isWeak']=bool(weak and str(r.get('side'))==weak);rows.append(r)
 by=defaultdict(list)
 for r in rows:
  if r['isWeak']:by[str(r.get('orderId'))].append(r)
 out={'version':'R4_ADAPTIVE_EXECUTION_LIVENESS_V1','researchOnly':True,'actionAuthority':False,'marketId':int(a.market_id),'makerQtyUnique':audit.get('makerQtyUnique'),'takerDynamicNon10Observed':audit.get('takerDynamicNon10Observed'),'orders':len(by),'weakRows':sum(len(v) for v in by.values()),'thresholds':{},'oversize':{}}
 for t in TH:
  episodes=[]
  for oid,seq in by.items():
   seq=sorted(seq,key=lambda r:int(r.get('checkpointMs') or 0));active=None
   for i,r in enumerate(seq):
    off=r.get('quoteOffsetTicks');off=float(off) if off is not None and math.isfinite(float(off)) else math.nan;pr=float(r.get('partialFillRatio') or 0.0);flag=bool(pr<=EPS and math.isfinite(off) and off>=t)
    tm=int(r.get('checkpointMs') or 0)
    if flag and active is None:
     active={'orderId':oid,'startMs':tm,'startAgeMs':float(r.get('orderAgeMs') or 0.0),'startOffset':off,'fill5s':int(r.get('labelAnyFill5s') or 0),'eventual':int(float(r.get('eventualAdditionalFillShares') or 0)>EPS)}
    if active is not None and (not flag or i==len(seq)-1):
     end=tm if not flag else tm;active['endMs']=end;active['durationMs']=max(0,end-int(active['startMs']));active['reverted']=bool(not flag);episodes.append(active);active=None
  if episodes:
   d=[e['durationMs'] for e in episodes]; out['thresholds'][str(t)]={'episodes':len(episodes),'orders':len(set(e['orderId'] for e in episodes)),'medianDurationMs':float(np.median(d)),'p90DurationMs':float(np.percentile(d,90)),'durationSumMs':int(sum(d)),'revertCount':int(sum(e['reverted'] for e in episodes)),'fill5sCount':int(sum(e['fill5s'] for e in episodes)),'eventualCount':int(sum(e['eventual'] for e in episodes)),'revertRate':float(np.mean([e['reverted'] for e in episodes])),'fill5sAtEpisodeStartRate':float(np.mean([e['fill5s'] for e in episodes])),'eventualFillAtEpisodeStartRate':float(np.mean([e['eventual'] for e in episodes])),'episodesPerOrder':float(len(episodes)/max(1,len(set(e['orderId'] for e in episodes))))}
  else:out['thresholds'][str(t)]={'episodes':0,'orders':0}
 # oversize: live weak-side remaining exceeds latest gap
 eps=[]
 for oid,seq in by.items():
  seq=sorted(seq,key=lambda r:int(r.get('checkpointMs') or 0));active=None
  for i,r in enumerate(seq):
   rem=float(r.get('remainingQty') or 0.0);gap=float(r.get('gap') or 0.0);flag=bool(gap>EPS and rem>gap+EPS);tm=int(r.get('checkpointMs') or 0)
   if flag and active is None:active={'orderId':oid,'startMs':tm,'excessStart':rem-gap,'fill5s':int(r.get('labelAnyFill5s') or 0)}
   if active is not None and (not flag or i==len(seq)-1):
    active['endMs']=tm;active['durationMs']=max(0,tm-int(active['startMs']));active['reverted']=bool(not flag);eps.append(active);active=None
 if eps:
  d=[e['durationMs'] for e in eps];out['oversize']={'episodes':len(eps),'orders':len(set(e['orderId'] for e in eps)),'medianDurationMs':float(np.median(d)),'p90DurationMs':float(np.percentile(d,90)),'durationSumMs':int(sum(d)),'revertCount':int(sum(e['reverted'] for e in eps)),'fill5sCount':int(sum(e['fill5s'] for e in eps)),'excessStartSumShares':float(sum(e['excessStart'] for e in eps)),'revertRate':float(np.mean([e['reverted'] for e in eps])),'fill5sAtEpisodeStartRate':float(np.mean([e['fill5s'] for e in eps])),'meanExcessStartShares':float(np.mean([e['excessStart'] for e in eps]))}
 else:out['oversize']={'episodes':0,'orders':0}
 Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'marketId':a.market_id,'orders':out['orders'],'weakRows':out['weakRows'],'thresholds':out['thresholds'],'oversize':out['oversize']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
