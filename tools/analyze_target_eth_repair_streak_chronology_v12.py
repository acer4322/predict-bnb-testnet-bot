from __future__ import annotations
import argparse,json,sqlite3,statistics
from collections import Counter
from pathlib import Path
import numpy as np
EPS=1e-9

def episodes(db,asset):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null",(asset,))}
 rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
 out=[];cur=None;u=d=0.;active=False;rep=0;last=None
 for r in rows:
  mid=int(r['market_id']);t=int(r['first_event_ms']);side=str(r['side'])
  if mid!=cur:cur=mid;u=d=0.;active=False;rep=0;last=None
  weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;typ='REPAIR' if weak is not None and side==weak else 'EXPAND'
  if typ=='EXPAND':
   if active and last is not None:out.append({'end':ends.get(mid),'repairCount':rep,'elapsedMs':t-last})
   active=True;rep=0;last=t
  elif active:rep+=1
  sh=float(r['shares']);u+=sh if side=='UP' else 0.;d+=sh if side=='DOWN' else 0.
 return out

def s(z):
 if not z:return {'n':0}
 c=[x['repairCount'] for x in z];tm=[x['elapsedMs'] for x in z];h=Counter('4+' if x>=4 else str(x) for x in c)
 return {'n':len(z),'medianRepair':float(statistics.median(c)),'meanRepair':float(statistics.mean(c)),'p0':h['0']/len(z),'p1':h['1']/len(z),'p2':h['2']/len(z),'p3':h['3']/len(z),'p4plus':h['4+']/len(z),'elapsedMedian':float(statistics.median(tm)),'elapsedP75':float(np.quantile(tm,.75))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();out={'version':'TARGET_ETH_REPAIR_STREAK_CHRONOLOGY_V12','assets':{}}
 for asset in ('ETH','BTC'):
  z=episodes(a.db,asset);ends=sorted(set(x['end'] for x in z if x['end'] is not None));cut=ends[int(len(ends)*.7)];early=[x for x in z if x['end'] is not None and x['end']<cut];late=[x for x in z if x['end'] is not None and x['end']>=cut];out['assets'][asset]={'cut':cut,'early':s(early),'late':s(late),'deltaP0':s(late)['p0']-s(early)['p0'],'deltaMeanRepair':s(late)['meanRepair']-s(early)['meanRepair']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True)
if __name__=='__main__':main()
