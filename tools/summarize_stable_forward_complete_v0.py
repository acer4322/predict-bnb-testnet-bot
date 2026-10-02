from __future__ import annotations
import json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
F=ROOT/'data'/'strategy_target_flash_v1.db'; B=ROOT/'data'/'strategy_target_compare_v1.db'; T=ROOT/'data'/'target_wallet_official_v1.db'
V='FLASH:STABLE_DIRECTIONAL_TOLERANCE_V1:R1'; START=1787094300000

def ro(p): c=sqlite3.connect(f'file:{p.resolve()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; return c

def pair(fs):
 u=[[float(x['shares']),float(x['price'])] for x in fs if x['side']=='UP']; d=[[float(x['shares']),float(x['price'])] for x in fs if x['side']=='DOWN'];i=j=0;ps=e=0.0
 while i<len(u) and j<len(d):
  q=min(u[i][0],d[j][0]);ps+=q;e+=q*(1-u[i][1]-d[j][1]);u[i][0]-=q;d[j][0]-=q
  if u[i][0]<=1e-9:i+=1
  if d[j][0]<=1e-9:j+=1
 return ps,e
f=ro(F);b=ro(B);t=ro(T)
try:
 mids=[dict(r) for r in f.execute('select market_id,max(seconds_left) maxsec,min(seconds_left) minsec,count(*) n from our_decisions where strategy_version=? and decision_ms>=? group by market_id having max(seconds_left)>=295',(V,START))]
 rows=[]
 for z in mids:
  m=int(z['market_id']); rr=t.execute('select winner from target_market_results where market_id=?',(m,)).fetchone()
  if not rr or rr['winner'] not in ('UP','DOWN'): continue
  win=rr['winner']; fs=[dict(r) for r in f.execute('select side,price,shares,channel,purpose from our_fills where strategy_version=? and market_id=?',(V,m))]; bs=[dict(r) for r in b.execute("select side,price,shares,channel,purpose from our_fills where market_id=? and strategy_version like 'UNIFIED_CONTROLLER_PAPER_V1%'",(m,))]
  def pnl(xs):
   cost=sum(float(x['price'])*float(x['shares']) for x in xs); pay=sum(float(x['shares']) for x in xs if x['side']==win); return pay-cost
  maker=[x for x in fs if x['channel']=='MAKER']; up=sum(float(x['shares']) for x in maker if x['side']=='UP');dn=sum(float(x['shares']) for x in maker if x['side']=='DOWN');ps,e=pair(maker)
  rows.append({'marketId':m,'pnl':pnl(fs),'basePnl':pnl(bs),'delta':pnl(fs)-pnl(bs),'positive':pnl(fs)>1e-9,'makerAbsNet':abs(up-dn),'pairedShares':ps,'pairEdge':e,'pairEdgePerShare':e/ps if ps else None})
 ed=sum(x['pairEdge'] for x in rows);ps=sum(x['pairedShares'] for x in rows); ds=sorted(x['delta'] for x in rows); nets=sorted(x['makerAbsNet'] for x in rows)
 out={'coverage':{'completeSettledMarkets':len(rows)},'pnl':sum(x['pnl'] for x in rows),'basePnl':sum(x['basePnl'] for x in rows),'delta':sum(x['delta'] for x in rows),'positiveMarkets':sum(x['positive'] for x in rows),'positiveRate':sum(x['positive'] for x in rows)/len(rows) if rows else None,'betterVsBase':sum(x['delta']>1e-9 for x in rows),'worseVsBase':sum(x['delta']<-1e-9 for x in rows),'medianDelta':ds[len(ds)//2] if ds else None,'worstPnl':min((x['pnl'] for x in rows),default=None),'worstDelta':min((x['delta'] for x in rows),default=None),'makerPairEdgePerShare':ed/ps if ps else None,'makerAbsNetMedian':nets[len(nets)//2] if nets else None,'makerAbsNetMax':max(nets) if nets else None,'rows':rows}
 print(json.dumps(out,ensure_ascii=False,indent=2))
finally:f.close();b.close();t.close()
