from __future__ import annotations
import sqlite3,json,os,math
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
PUB=ROOT/'data/public_source_snapshot_archive_v2.db'; MKR=ROOT/'data/wallet_maker_book_inference.db'; OFF=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_REPAIR_TAKER_CHURN_ECONOMIC_CEILING_V32P_RESULT.json'
def main():
 cp=sqlite3.connect(f'file:{PUB.resolve().as_posix()}?mode=ro',uri=True); cp.row_factory=sqlite3.Row
 cm=sqlite3.connect(f'file:{MKR.resolve().as_posix()}?mode=ro',uri=True); cm.row_factory=sqlite3.Row
 co=sqlite3.connect(f'file:{OFF.resolve().as_posix()}?mode=ro',uri=True); co.row_factory=sqlite3.Row
 pm={int(x[0]) for x in cp.execute('select distinct market_id from public_source_snapshots_v2 where market_id>1399926')}; vm={int(x[0]) for x in cm.execute('select distinct market_id from maker_book_inference_v21_market_meta where market_id>1399926')}; om={int(x[0]) for x in co.execute("select distinct market_id from target_markets where asset='BTC' and market_id>1399926")}; markets=sorted(pm&vm&om)[240:293]
 rows=[]
 for mid in markets:
  snaps=[]
  for rr in cp.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
   t=int(rr['sampled_at_ms']); b=t//1000*1000; j=json.loads(rr['snapshot_json'])
   if snaps and snaps[-1][0]==b:snaps[-1]=(b,t,j)
   else:snaps.append((b,t,j))
  ev=[dict(r) for r in co.execute("select event_ms,role,side,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' order by event_ms,id",(mid,))]
  tb=defaultdict(list)
  for e in ev:
   if e['role']=='TAKER':tb[int(e['event_ms'])//1000*1000].append(e['side'])
  canc=[dict(r) for r in cm.execute('select target_side,cancel_source_ms from maker_book_inference_v21_cancel_candidates where market_id=? order by cancel_source_ms',(mid,))]
  i=0;up=dn=cost=0.0
  for b,t,j in snaps:
   while i<len(ev) and int(ev[i]['event_ms'])<b:
    e=ev[i];q=float(e['shares']);p=float(e['price']);cost+=p*q;up+=q if e['side']=='UP' else 0;dn+=q if e['side']=='DOWN' else 0;i+=1
   if abs(up-dn)<=1e-9 or min(up,dn)-cost>=-1e-9:continue
   weak='UP' if up<dn else 'DOWN'; S=max(up,dn); gap=abs(up-dn); cap=(S-cost)/gap
   ask=j.get('predictUpAsk' if weak=='UP' else 'predictDownAsk'); sec=j.get('secondsLeft')
   if ask is None or sec is None:continue
   churn10=sum(1 for x in canc if x['target_side']==weak and t-10000<=int(x['cancel_source_ms'])<t)
   ask=float(ask); y=1 if weak in tb.get(b+1000,[]) else 0
   rows.append({'marketId':mid,'secondsLeft':float(sec),'weakSide':weak,'economicRepairCeiling':cap,'weakAsk':ask,'askGap':ask-cap,'disconnect':ask>cap,'churn10':churn10,'label':y})
 cp.close();cm.close();co.close()
 pos=[r for r in rows if r['label']]; churn=[r for r in rows if r['churn10']>0]; churnpos=[r for r in churn if r['label']]; y=np.array([r['label'] for r in churn]); s=np.array([r['askGap'] for r in churn])
 auc=float(roc_auc_score(y,s)) if len(set(y.tolist()))>1 else None
 recall=len(churnpos)/len(pos) if pos else None; posdisc=sum(r['disconnect'] for r in churnpos)/len(churnpos) if churnpos else None; basedisc=sum(r['disconnect'] for r in churn)/len(churn) if churn else None; delta=(posdisc-basedisc) if posdisc is not None and basedisc is not None else None
 adequate=len(pos)>=20; keep=adequate and recall>=.95 and auc is not None and auc>=.60 and delta>=.10
 # descriptive time bins
 bins=[]
 for lo,hi in [(240,301),(180,240),(120,180),(60,120),(30,60),(0,30)]:
  z=[r for r in rows if lo<=r['secondsLeft']<hi]; zp=[r for r in z if r['label']]; zc=[r for r in z if r['churn10']>0]; zcp=[r for r in zc if r['label']]
  bins.append({'secondsLeft':[lo,hi],'states':len(z),'positives':len(zp),'churn10Recall':len(zcp)/len(zp) if zp else None,'churn10States':len(zc),'churn10Positives':len(zcp),'churn10AskGapAuc':float(roc_auc_score([r['label'] for r in zc],[r['askGap'] for r in zc])) if zc and len(set(r['label'] for r in zc))>1 else None,'positiveDisconnectRate':sum(r['disconnect'] for r in zcp)/len(zcp) if zcp else None,'churnStateDisconnectRate':sum(r['disconnect'] for r in zc)/len(zc) if zc else None})
 out={'version':'TARGET_BTC_REPAIR_TAKER_CHURN_ECONOMIC_CEILING_V32P_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'preregistered':'TARGET_BTC_REPAIR_TAKER_CHURN_ECONOMIC_CEILING_V32P_PREREGISTERED.json','marketIds':markets,'rows':len(rows),'positives':len(pos),'churn10States':len(churn),'churn10Positives':len(churnpos),'checks':{'supportAdequate':adequate,'churn10Recall':recall,'askGapAucWithinChurn10':auc,'repairTakerDisconnectRateWithinChurn10':posdisc,'churn10StateDisconnectRate':basedisc,'disconnectRateLift':delta},'decision':'KEEP_CHURN_PLUS_ECONOMIC_CEILING_ACTIVE_READINESS_SHADOW' if keep else ('TESTED_INCONCLUSIVE' if not adequate else 'REJECT_CHURN_PLUS_ECONOMIC_CEILING_ACTIVE_READINESS'),'timeBins':bins,'boundary':['final disjoint later BTC common cohort','BTC architecture only','no numeric transfer to ETH','no threshold fitting','no winner/PnL','not Taker submit authority','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
