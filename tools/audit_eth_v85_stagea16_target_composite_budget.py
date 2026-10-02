from __future__ import annotations
import json,sqlite3,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'data/target_wallet_official_v1.db';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V85_STAGEA16_COMPOSITE_BUDGET_20260903.json'
MIDS=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768]
con=sqlite3.connect(DB);con.row_factory=sqlite3.Row;allc=[];per=[]
for mid in MIDS:
 rs=con.execute("select role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(mid,)).fetchall();u=d=cost=0.0;c=[]
 for r in rs:
  q=float(r['shares'] or 0);p=float(r['average_price'] or 0);side=str(r['side']);gap=abs(u-d);weak='UP' if u<d-1e-9 else 'DOWN' if d<u-1e-9 else None;rq=min(q,gap) if side==weak else 0.0;eq=q-rq
  if rq>1e-9 and eq>1e-9:
   z={'marketId':mid,'t':int(r['first_event_ms']),'role':str(r['role']),'side':side,'price':p,'qty':q,'notional':q*p,'venueMinQty':1.0/p if p>0 else None,'qtyToVenueMin':q*p if p>0 else None,'repairAllocation':rq,'overflowAllocation':eq,'overflowToVenueMin':eq/(1.0/p) if p>0 else None,'repairGapPre':gap};c.append(z);allc.append(z)
  if side=='UP':u+=q
  else:d+=q
  cost+=q*p
 per.append({'marketId':mid,'compositeParents':len(c),'maker':sum(x['role']=='MAKER' for x in c),'taker':sum(x['role']=='TAKER' for x in c)})
con.close()
def med(a):return statistics.median(a) if a else None
def pct(a,p):
 if not a:return None
 a=sorted(a);return a[min(len(a)-1,max(0,int(round((len(a)-1)*p))))]
ov=[x['overflowAllocation'] for x in allc];q=[x['qty'] for x in allc];rq=[x['repairAllocation'] for x in allc];notional=[x['notional'] for x in allc]
res={'version':'TARGET_ETH_V85_STAGEA16_COMPOSITE_BUDGET','date':'2026-09-03','researchOnly':True,'markets':MIDS,'summary':{'compositeParents':len(allc),'marketsWithComposite':sum(x['compositeParents']>0 for x in per),'makerComposite':sum(x['role']=='MAKER' for x in allc),'takerComposite':sum(x['role']=='TAKER' for x in allc),'totalQtyMedian':med(q),'repairAllocationMedian':med(rq),'overflowMedian':med(ov),'overflowP25':pct(ov,.25),'overflowP75':pct(ov,.75),'overflowGt1Share':sum(x>1 for x in ov)/len(ov) if ov else 0,'overflowGt2Share':sum(x>2 for x in ov)/len(ov) if ov else 0,'overflowGt3Share':sum(x>3 for x in ov)/len(ov) if ov else 0,'parentNotionalMedian':med(notional),'parentNotionalP25':pct(notional,.25),'parentNotionalP75':pct(notional,.75),'parentNotionalGt1Share':sum(x>1.000001 for x in notional)/len(notional) if notional else 0},'perMarket':per,'rows':allc,'boundary':['Target actual fills post-market only','No Target qty becomes runtime sizing authority','Descriptive budget anatomy only']}
OUT.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':res['summary'],'perMarket':per}))
