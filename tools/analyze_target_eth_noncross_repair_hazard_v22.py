from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
import numpy as np
EPS=1e-9

def load(db):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 rs=list(c.execute("select market_id,side,first_event_ms,shares from target_parent_orders where asset='ETH' and role='MAKER' order by market_id,first_event_ms,parent_id"));c.close();return rs

def build(db):
 rs=load(db);mts=[];seen=set()
 for r in rs:
  m=int(r['market_id'])
  if m not in seen:mts.append(int(r['first_event_ms']));seen.add(m)
 cut=int(np.median(mts)) if mts else 0
 out=[];cur=None;u=d=0.;prev=None
 for r in rs:
  mid=int(r['market_id']);t=int(r['first_event_ms']);side=str(r['side']);q=float(r['shares'])
  if mid!=cur:cur=mid;u=d=0.;prev=None
  pre=u-d;gap=abs(pre);weak='UP' if pre< -EPS else 'DOWN' if pre>EPS else None;role='REPAIR' if weak is not None and side==weak else 'EXPAND'
  if side=='UP':u+=q
  else:d+=q
  post=u-d;postgap=abs(post);cross=(pre*post< -EPS) or (gap>EPS and postgap<=EPS)
  frac=(gap-postgap)/gap if role=='REPAIR' and gap>EPS else None
  rec={'t':t,'role':role,'cross':cross,'frac':frac}
  if prev is not None and prev['role']=='REPAIR' and not prev['cross'] and prev['frac'] is not None:
   out.append({'t':t,'switch':1 if role=='EXPAND' else 0,'frac':prev['frac'],'chron':'early' if t<=cut else 'late'})
  prev=rec
 return out,cut

def summ(z):
 bins=[(-9,0,'<=0'),(0,.25,'0-25%'),(.25,.5,'25-50%'),(.5,.75,'50-75%'),(.75,1.000001,'75-100%'),(1.000001,99,'100%+')]
 o={'n':len(z),'overallSwitchRate':float(np.mean([r['switch'] for r in z])) if z else None,'bins':{}}
 for lo,hi,nm in bins:
  a=[r for r in z if r['frac']>=lo and r['frac']<hi]
  o['bins'][nm]={'n':len(a),'switchRate':float(np.mean([r['switch'] for r in a])) if a else None,'medianFrac':float(np.median([r['frac'] for r in a])) if a else None}
 return o

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();z,cut=build(a.db)
 out={'version':'TARGET_ETH_NONCROSS_REPAIR_HAZARD_V22','researchOnly':True,'boundary':['Target ETH Maker actual-filled parent sequence only','No Target numeric threshold copied into OUR','Conditions on preceding REPAIR parent not crossing inventory sign; tests structural monotonicity of repair progress vs next-phase switch hazard','Early/late chronology stability required'],'cut':cut,'all':summ(z),'early':summ([r for r in z if r['chron']=='early']),'late':summ([r for r in z if r['chron']=='late'])}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
