from __future__ import annotations
import csv,json,math,os,sqlite3,statistics,time
from collections import deque
from pathlib import Path
import httpx
from predict_bot.core import taker_fee
from predict_bot.target_wallet_official_v1 import API_BASE,resolved_winner
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'; TDB=ROOT/'data'/'target_wallet_official_v1.db'; HFTDB=ROOT/'data'/'hft_forward_paper_v1.db'; OUT=ROOT/'data'/'research'
REPORT=OUT/'8784_r2_vs_8786_cap100_fresh_v1_report.json'; CSV=OUT/'8784_r2_vs_8786_cap100_fresh_v1_markets.csv'; CACHE=OUT/'8784_promoted_settlement_cache_v1.json'
V4='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'; V6='UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER'; EPS=1e-9; FEE_BPS=200

def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=20); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c
def sj(x):
 try:
  z=json.loads(str(x)); return z if isinstance(z,dict) else {}
 except: return {}
def qs(xs):
 y=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not y:return {'n':0,'mean':None,'median':None,'min':None,'max':None,'p25':None,'p75':None,'sum':0.0}
 def q(p):
  if len(y)==1:return y[0]
  z=(len(y)-1)*p; lo=math.floor(z); hi=math.ceil(z); w=z-lo; return y[lo]*(1-w)+y[hi]*w
 return {'n':len(y),'mean':statistics.mean(y),'median':statistics.median(y),'min':y[0],'max':y[-1],'p25':q(.25),'p75':q(.75),'sum':sum(y)}
def fifo(fs):
 q={'UP':deque(),'DOWN':deque()}
 for f in sorted(fs,key=lambda x:(int(x['filled_at_ms']),str(x['fill_id']))): q[str(f['side'])].append([float(f['shares']),float(f['price'])])
 paired=edge=0.0
 while q['UP'] and q['DOWN']:
  u,d=q['UP'][0],q['DOWN'][0]; x=min(u[0],d[0]); paired+=x; edge+=x*(1-u[1]-d[1]); u[0]-=x; d[0]-=x
  if u[0]<=EPS:q['UP'].popleft()
  if d[0]<=EPS:q['DOWN'].popleft()
 return paired,edge
def api_key():
 v=str(os.environ.get('PREDICT_FUN_API_KEY') or '').strip()
 if v:return v
 try:
  import winreg
  with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Environment') as k:return str(winreg.QueryValueEx(k,'PREDICT_FUN_API_KEY')[0] or '').strip()
 except:return ''
def reconstruct_commit_peak(orders):
 ev=[]
 for o in orders:
  n=float(o['price'])*float(o['shares']); ev.append((int(o['placed_at_ms']),1,n))
  end=o['filled_at_ms'] if o['filled_at_ms'] is not None else o['cancelled_at_ms']
  if end is not None:ev.append((int(end),0,-n))
 cur=peak=0.0
 for _,kind,delta in sorted(ev,key=lambda z:(z[0],z[1])):cur=max(0.0,cur+delta); peak=max(peak,cur)
 return peak,cur
def settled_winner(mid,tcon,cache,client,rcon):
 r=tcon.execute("select window_end_ms,winner,status from target_markets where market_id=? and asset='BTC'",(mid,)).fetchone()
 end=int(r['window_end_ms']) if r and r['window_end_ms'] is not None else None; w=str(r['winner']) if r and r['winner'] in ('UP','DOWN') else None
 if end is None:
  for rr in rcon.execute('select public_state_json from our_decisions where market_id=? and strategy_version in (?,?) order by decision_ms limit 8',(mid,V4,V6)):
   z=sj(rr[0]); x=z.get('windowEndMs') or z.get('window_end_ms')
   try:
    if x is not None:end=int(x); break
   except:pass
 if not w and isinstance(cache.get(str(mid)),dict) and cache[str(mid)].get('winner') in ('UP','DOWN'):w=str(cache[str(mid)]['winner'])
 if not w and end and int(time.time()*1000)>=end+2000:
  try:
   z=client.get(f'{API_BASE}/v1/markets/{mid}'); z.raise_for_status(); p=z.json(); m=p.get('data') if isinstance(p,dict) and isinstance(p.get('data'),dict) else p; ww=resolved_winner(m) if isinstance(m,dict) else None
   cache[str(mid)]={'marketId':mid,'winner':ww,'fetchedAtMs':int(time.time()*1000)}
   if ww in ('UP','DOWN'):w=ww
  except Exception:pass
 return w,end
def one(con,v,mid,w,end):
 fs=[dict(r) for r in con.execute('select * from our_fills where strategy_version=? and market_id=? order by filled_at_ms,fill_id',(v,mid))]
 os_=[dict(r) for r in con.execute('select * from our_orders where strategy_version=? and market_id=? order by placed_at_ms,order_id',(v,mid))]
 ds=[dict(r) for r in con.execute('select * from our_decisions where strategy_version=? and market_id=? order by decision_ms,decision_id',(v,mid))]
 maker=[f for f in fs if str(f['channel']).upper()=='MAKER']; taker=[f for f in fs if str(f['channel']).upper()=='TAKER']; up=sum(float(f['shares']) for f in fs if f['side']=='UP'); dn=sum(float(f['shares']) for f in fs if f['side']=='DOWN'); mu=sum(float(f['shares']) for f in maker if f['side']=='UP'); md=sum(float(f['shares']) for f in maker if f['side']=='DOWN')
 maker_spent=sum(float(f['price'])*float(f['shares']) for f in maker); taker_spent=sum(float(f['price'])*float(f['shares']) for f in taker); fees=sum(taker_fee(float(f['shares']),float(f['price']),FEE_BPS) for f in taker); spent=maker_spent+taker_spent+fees; pnl=((up if w=='UP' else dn)-maker_spent-taker_spent-fees) if w else None
 paired,edge=fifo(maker); gross=mu+md; cov=2*min(mu,md)/gross if gross>EPS else None; peakcomm,endcomm=reconstruct_commit_peak(os_)
 acts={'RESIDUAL_ARBITRATION_WAKE':0,'EXCURSION_RECOVERED':0,'UNRESOLVED_GUARD_ENTER':0,'TAKER_READINESS_LATCH':0,'PASSIVE_REPAIR_PRIORITY':0,'TAKER_INTERVENE':0,'MAKER_BURST':0,'TAKER_CAP_BLOCK':0}; cap_samples=[]
 for d in ds:
  p=sj(d['payload_json']);
  for a in p.get('actions',[]) if isinstance(p.get('actions'),list) else []:
   if isinstance(a,dict) and str(a.get('action')) in acts:acts[str(a['action'])]+=1
  if isinstance(p.get('capital'),dict):cap_samples.append(p['capital'])
 open_cut=end-240000 if end else None
 max_wc=max([float(x.get('worstCaseCommittedUsdt',0) or 0) for x in cap_samples] or [0]); min_mrem=min([float(x.get('makerRemainingUsdt',999) or 999) for x in cap_samples] or [999]); cap_pressure=sum(1 for x in cap_samples if float(x.get('makerRemainingUsdt',999) or 999)<18*0.99)
 return {'pnl':pnl,'positive':int(pnl>0) if pnl is not None else None,'spent':spent,'makerSpent':maker_spent,'takerReserveUsed':taker_spent+fees,'makerCommittedPeak':peakcomm,'makerCommittedEnd':endcomm,'worstCaseCommittedPeak':max_wc,'makerCapPressureDecisionProxy':cap_pressure,'makerCapBlocksExactAvailable':False,'takerCapBlocks':acts['TAKER_CAP_BLOCK'],'makerPlacements':len(os_),'makerFills':len(maker),'takerFills':len(taker),'open60Placements':sum(1 for o in os_ if open_cut and int(o['placed_at_ms'])<=open_cut),'open60Fills':sum(1 for f in maker if open_cut and int(f['filled_at_ms'])<=open_cut),'pairedShares':paired,'lockedEdge':edge,'pairCoverage':cov,'finalMakerAbsNet':abs(mu-md),'finalAbsNet':abs(up-dn),'residualWakes':acts['RESIDUAL_ARBITRATION_WAKE'],'residualRecoveries':acts['EXCURSION_RECOVERED'],'unresolvedGuard':acts['UNRESOLVED_GUARD_ENTER'],'passivePriority':acts['PASSIVE_REPAIR_PRIORITY'],'takerInterventions':acts['TAKER_INTERVENE'],'makerBurst':acts['MAKER_BURST']}
def main():
 c=ro(DB); t=ro(TDB)
 hft_cutover_end=None
 if HFTDB.exists():
  try:
   hc=ro(HFTDB)
   try:
    rr=hc.execute("select value from hft_forward_meta_v1 where key='activation_after_window_end_ms'").fetchone()
    if rr is not None:hft_cutover_end=int(rr[0])
   finally:hc.close()
  except Exception:pass
 try:cache=json.loads(CACHE.read_text(encoding='utf-8'))
 except:cache={}
 key=api_key(); client=httpx.Client(timeout=httpx.Timeout(8,connect=2),trust_env=False,headers={'Accept':'application/json',**({'x-api-key':key} if key else {})})
 try:
  sets=[]
  for v in (V4,V6):sets.append({int(r[0]) for r in c.execute('select distinct market_id from our_decisions where strategy_version=?',(v,))})
  common=sorted(set.intersection(*sets)); rows=[]
  for mid in common:
   w,end=settled_winner(mid,t,cache,client,c)
   if not w:continue
   if hft_cutover_end is not None and end is not None and int(end)>int(hft_cutover_end):continue
   a=one(c,V4,mid,w,end); b=one(c,V6,mid,w,end); rows.append({'marketId':mid,'winner':w,**{f'r2_{k}':v for k,v in a.items()},**{f'cap_{k}':v for k,v in b.items()},'pnlDeltaCapMinusR2':b['pnl']-a['pnl']})
  def agg(prefix):
   rs=rows; g=lambda k:[r[f'{prefix}_{k}'] for r in rs]
   return {'pnl':qs(g('pnl')),'positiveMarkets':sum(g('positive')),'positiveRate':sum(g('positive'))/len(rs) if rs else None,'capitalSpent':qs(g('spent')),'makerSpent':qs(g('makerSpent')),'takerReserveUsed':qs(g('takerReserveUsed')),'makerCommittedPeak':qs(g('makerCommittedPeak')),'makerCommittedEnd':qs(g('makerCommittedEnd')),'makerPlacements':qs(g('makerPlacements')),'makerFills':qs(g('makerFills')),'takerFills':qs(g('takerFills')),'open60Placements':qs(g('open60Placements')),'open60Fills':qs(g('open60Fills')),'pairedShares':qs(g('pairedShares')),'lockedEdge':qs(g('lockedEdge')),'pairCoverage':qs(g('pairCoverage')),'finalMakerAbsNet':qs(g('finalMakerAbsNet')),'finalAbsNet':qs(g('finalAbsNet')),'residualWakes':sum(g('residualWakes')),'residualRecoveries':sum(g('residualRecoveries')),'unresolvedGuard':sum(g('unresolvedGuard')),'takerInterventions':sum(g('takerInterventions'))}
  A=agg('r2'); B=agg('cap'); edgeB=sum(r['cap_lockedEdge'] for r in rows); pairB=sum(r['cap_pairedShares'] for r in rows)
  report={'reportVersion':'8784_R2_VS_8786_CAP100_FRESH_V1','researchOnly':True,'officialPerformanceEvidence':False,'executionEvidenceLabel':'LEGACY_QUEUECLEAR_DIAGNOSTIC_ONLY','hftForwardCutoverAfterWindowEndMs':hft_cutover_end,'postCutoverPolicy':'EXCLUDED_USE_HFT_FORWARD_PAPER_V1','matchedSettledMarkets':len(rows),'commonObservedMarkets':len(common),'r2':A,'cap100':B,'matchedDelta':{'pnlCapMinusR2':qs([r['pnlDeltaCapMinusR2'] for r in rows]),'capBetterMarkets':sum(r['pnlDeltaCapMinusR2']>0 for r in rows),'capWorseMarkets':sum(r['pnlDeltaCapMinusR2']<0 for r in rows)},'capSafety':{'makerBudgetUsdt':80,'takerReserveUsdt':20,'totalCapUsdt':100,'makerCapBlocksExactHistoricalAvailable':False,'makerCapPressureDecisionProxy':sum(r['cap_makerCapPressureDecisionProxy'] for r in rows),'takerCapBlocksExact':sum(r['cap_takerCapBlocks'] for r in rows),'makerLockedEdgePerPairedShare':edgeB/pairB if pairB>EPS else None,'note':'makerCapBlocks counter is runtime-only and not persisted per decision; historical report uses capital-pressure decision proxy and reconstructs resting commitment peak from order lifetimes.'},'classification':(
   'CAP100_EDGE_PRESERVED_CANDIDATE' if len(rows)>=25 and B['pnl']['sum']>0 and B['positiveRate'] is not None and B['positiveRate']>=0.35 and (edgeB/pairB if pairB>EPS else -1)>=0 and B['finalMakerAbsNet']['max']<=A['finalMakerAbsNet']['max'] and sum(r['pnlDeltaCapMinusR2'] for r in rows)>=0 and statistics.median(r['pnlDeltaCapMinusR2'] for r in rows)>=0 and sum(r['pnlDeltaCapMinusR2']>0 for r in rows)>=sum(r['pnlDeltaCapMinusR2']<0 for r in rows)
   else 'CAP100_SAFETY_CONTAINMENT_EDGE_NOT_PRESERVED' if len(rows)>=25 and B['pnl']['sum']>0 and B['positiveRate'] is not None and B['positiveRate']>=0.35 and (edgeB/pairB if pairB>EPS else -1)>=0 and B['finalMakerAbsNet']['max']<=A['finalMakerAbsNet']['max']
   else 'ACCUMULATING_SMALL_SAMPLE'
  ),'guards':['Only markets with both strategy versions observed and post-hoc resolved winner are included.','Winner/Target settlement is evaluation-only and never runtime input.','8784 R2 and 8786 CAP100 remain frozen; no parameter selection performed.','Small sample cannot establish CAP100 edge preservation.','After HFT Forward PAPER cutover, new QUEUECLEAR markets are excluded from this analyzer and are diagnostic-only.']}
  OUT.mkdir(parents=True,exist_ok=True); REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  with CSV.open('w',newline='',encoding='utf-8-sig') as f:
   if rows:w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
  CACHE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
 finally:client.close(); c.close(); t.close()
if __name__=='__main__':main()
