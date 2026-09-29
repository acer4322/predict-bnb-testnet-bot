from __future__ import annotations
import sqlite3,json,bisect,statistics,math
from pathlib import Path
from collections import defaultdict
ROOT=Path.cwd().resolve();P=ROOT/'data/research/r4_v0/p0_provenance_v1'
FC=P/'eth_fresh150_inference_compact_v1.db';DB=ROOT/'data/wallet_maker_book_inference_eth5m.db';OUT=P/'TARGET_ETH_REPAIR_TAKER_PASSIVE_OPTION_EXHAUSTION_ANATOMY_V1.json'
OFFSETS=[-10000,-5000,-3000,-1000,-1]

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(z);hi=min(lo+1,len(ys)-1);w=z-lo;return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

def main():
 fc=sqlite3.connect(f'file:{FC.resolve().as_posix()}?mode=ro',uri=True);fc.row_factory=sqlite3.Row
 ids=[int(r[0]) for r in fc.execute('select market_id from maker_book_inference_markets order by market_id')]
 wallet=[dict(r) for r in fc.execute('select market_id,role,side,event_ms,price,shares,order_hash from maker_book_inference_wallet_events order by market_id,event_ms')];fc.close()
 ph=','.join('?'*len(ids));con=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 parents=[dict(r) for r in con.execute(f'''select parent_id,market_id,target_side,target_price,placement_first_ms,last_target_ms,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=0.75 and placement_coverage>=0.85 and fill_allocation_coverage>=0.70''',ids)];con.close()
 # Strict-past cumulative inventory by event timestamp. Same-timestamp fills are all excluded for a Taker at that timestamp.
 evb=defaultdict(list)
 for r in wallet:evb[int(r['market_id'])].append((int(r['event_ms']),str(r['side']),float(r['shares'])))
 pref={}
 for m,evs in evb.items():
  bytime=defaultdict(lambda:[0.,0.])
  for t,s,q in evs:bytime[t][0 if s=='UP' else 1]+=q
  ts=[];us=[];ds=[];u=d=0.
  for t in sorted(bytime):
   ts.append(t);u+=bytime[t][0];d+=bytime[t][1];us.append(u);ds.append(d)
  pref[m]=(ts,us,ds)
 def inv_before(m,t):
  x=pref.get(m)
  if not x:return 0.,0.
  ts,us,ds=x;i=bisect.bisect_left(ts,t)-1
  return (us[i],ds[i]) if i>=0 else (0.,0.)
 # Group Taker legs into parents.
 tg=defaultdict(float)
 for r in wallet:
  if r['role']!='TAKER':continue
  k=(int(r['market_id']),str(r['order_hash']),int(r['event_ms']),str(r['side']),round(float(r['price']),12));tg[k]+=float(r['shares'])
 takers=[]
 for (m,h,t,s,p),q in sorted(tg.items(),key=lambda z:(z[0][0],z[0][2])):
  u,d=inv_before(m,t)
  if abs(u-d)<=1e-9:role='FLAT'
  else:
   weak='UP' if u<d else 'DOWN';role='REPAIR' if s==weak else 'ADD'
  takers.append({'marketId':m,'orderHash':h,'t':t,'side':s,'price':p,'shares':q,'preUp':u,'preDown':d,'preAbsNet':abs(u-d),'role':role})
 pb=defaultdict(list)
 for r in parents:
  pb[(int(r['market_id']),str(r['target_side']))].append({'id':r['parent_id'],'p':float(r['target_price']),'a':int(r['placement_first_ms']),'b':max(int(r['placement_first_ms'])+1,int(r['last_target_ms'] or r['placement_first_ms'])+1)})
 def state(m,s,t):
  arr=pb.get((m,s),[]);act=[x for x in arr if x['a']<=t<x['b']]
  return {'parents':len(act),'distinct':len({round(x['p'],12) for x in act})}
 def count_window(m,s,t,w,kind):
  arr=pb.get((m,s),[]);lo=t-w
  if kind=='start':return sum(lo<=x['a']<t for x in arr)
  return sum(lo<=x['b']<t for x in arr)
 rows=[];seenRepair=set()
 for z in takers:
  r=dict(z)
  for off in OFFSETS:
   st=state(z['marketId'],z['side'],z['t']+off);tag=f'm{abs(off)//1000}s' if off<=-1000 else 'pre'
   r[f'activeParents_{tag}']=st['parents'];r[f'activeDistinct_{tag}']=st['distinct']
  for w in (1000,3000,10000):
   r[f'parentStarts_{w//1000}s']=count_window(z['marketId'],z['side'],z['t'],w,'start')
   r[f'parentEnds_{w//1000}s']=count_window(z['marketId'],z['side'],z['t'],w,'end')
  r['distinctDelta5sToPre']=r['activeDistinct_pre']-r['activeDistinct_m5s'];r['distinctDelta3sToPre']=r['activeDistinct_pre']-r['activeDistinct_m3s'];r['distinctDelta1sToPre']=r['activeDistinct_pre']-r['activeDistinct_m1s']
  if r['role']=='REPAIR':r['firstRepairTakerInMarket']=r['marketId'] not in seenRepair;seenRepair.add(r['marketId'])
  else:r['firstRepairTakerInMarket']=False
  rows.append(r)
 def block(rr):
  if not rr:return {'n':0}
  return {'n':len(rr),'markets':len({x['marketId'] for x in rr}),'preDistinct0Rate':sum(x['activeDistinct_pre']==0 for x in rr)/len(rr),'preDistinct1Rate':sum(x['activeDistinct_pre']==1 for x in rr)/len(rr),'preDistinct2PlusRate':sum(x['activeDistinct_pre']>=2 for x in rr)/len(rr),'activeDistinct_m10s':stats([x['activeDistinct_m10s'] for x in rr]),'activeDistinct_m5s':stats([x['activeDistinct_m5s'] for x in rr]),'activeDistinct_m3s':stats([x['activeDistinct_m3s'] for x in rr]),'activeDistinct_m1s':stats([x['activeDistinct_m1s'] for x in rr]),'activeDistinct_pre':stats([x['activeDistinct_pre'] for x in rr]),'distinctDelta5sToPre':stats([x['distinctDelta5sToPre'] for x in rr]),'distinctDelta3sToPre':stats([x['distinctDelta3sToPre'] for x in rr]),'distinctDelta1sToPre':stats([x['distinctDelta1sToPre'] for x in rr]),'parentStarts1s':stats([x['parentStarts_1s'] for x in rr]),'parentEnds1s':stats([x['parentEnds_1s'] for x in rr]),'parentStarts3s':stats([x['parentStarts_3s'] for x in rr]),'parentEnds3s':stats([x['parentEnds_3s'] for x in rr]),'parentStarts10s':stats([x['parentStarts_10s'] for x in rr]),'parentEnds10s':stats([x['parentEnds_10s'] for x in rr]),'anyEnd1sRate':sum(x['parentEnds_1s']>0 for x in rr)/len(rr),'anyEnd3sRate':sum(x['parentEnds_3s']>0 for x in rr)/len(rr),'anyStart1sRate':sum(x['parentStarts_1s']>0 for x in rr)/len(rr),'anyStart3sRate':sum(x['parentStarts_3s']>0 for x in rr)/len(rr)}
 repair=[x for x in rows if x['role']=='REPAIR'];add=[x for x in rows if x['role']=='ADD'];flat=[x for x in rows if x['role']=='FLAT'];first=[x for x in repair if x['firstRepairTakerInMarket']]
 out={'version':'TARGET_ETH_REPAIR_TAKER_PASSIVE_OPTION_EXHAUSTION_ANATOMY_V1','researchOnly':True,'actionAuthority':False,'coverage':{'freshMarkets':len(ids),'makerParents':len(parents),'takerParents':len(takers),'repairTakerParents':len(repair),'addTakerParents':len(add),'flatTakerParents':len(flat)},'summary':{'REPAIR':block(repair),'ADD':block(add),'FIRST_REPAIR_PER_MARKET':block(first),'FLAT':block(flat)},'rows':rows,'boundary':['Target Maker active-parent state is a retrospective lifecycle proxy using placement_first_ms through last_target_ms; it is anatomy, not a strict-past runtime feature.','Lifecycle end is not proof of private cancel.','Strict-past Taker role uses only wallet fills with event_ms strictly earlier than the Taker timestamp.','No PnL tuning; no BTC numeric transfer; no runtime action change.']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT),'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
