from __future__ import annotations
import json,sqlite3,bisect,math,statistics
from pathlib import Path
from collections import defaultdict
ROOT=Path.cwd().resolve();P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'target_eth_fresh150_no18_placement_v2_result.json';FC=P/'eth_fresh150_inference_compact_v1.db';OUT=P/'TARGET_ETH_NO18_REPAIR_MAKER_TRANCHE_ANATOMY_V1.json'

def qt(xs,q):
 y=sorted(float(v) for v in xs if v is not None and math.isfinite(float(v)))
 if not y:return None
 z=(len(y)-1)*q;i=int(z);j=min(i+1,len(y)-1);w=z-i;return y[i]*(1-w)+y[j]*w

def st(xs):
 y=[float(v) for v in xs if v is not None and math.isfinite(float(v))]
 return {'n':len(y),'mean':statistics.mean(y) if y else None,'median':statistics.median(y) if y else None,'p25':qt(y,.25),'p75':qt(y,.75),'p90':qt(y,.9)}
def main():
 d=json.loads(SRC.read_text(encoding='utf-8'));parents=[dict(x) for x in d['rows'] if x.get('highConfidencePlacement') and x.get('placementCarrierReadyMs') is not None]
 con=sqlite3.connect(f'file:{FC.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
 wallet=[dict(r) for r in con.execute('select market_id,role,side,event_ms,shares,order_hash from maker_book_inference_wallet_events order by market_id,event_ms')];con.close()
 # Inventory prefixes grouped by timestamp (strictly before placement timestamp).
 bym=defaultdict(list);lastMakerFill={}
 for r in wallet:
  m=int(r['market_id']);t=int(r['event_ms']);s=str(r['side']);q=float(r['shares']);bym[m].append((t,s,q))
  if r['role']=='MAKER':lastMakerFill[(m,str(r['order_hash']))]=max(t,lastMakerFill.get((m,str(r['order_hash'])),-1))
 pref={}
 for m,evs in bym.items():
  bytime=defaultdict(lambda:[0.,0.])
  for t,s,q in evs:bytime[t][0 if s=='UP' else 1]+=q
  ts=[];us=[];ds=[];u=dv=0.
  for t in sorted(bytime):
   ts.append(t);u+=bytime[t][0];dv+=bytime[t][1];us.append(u);ds.append(dv)
  pref[m]=(ts,us,ds)
 def inv_before(m,t):
  x=pref.get(m)
  if not x:return 0.,0.
  ts,us,ds=x;i=bisect.bisect_left(ts,t)-1
  return (us[i],ds[i]) if i>=0 else (0.,0.)
 # no18 active-parent proxy: placementCarrierReadyMs through last official Maker fill for same order hash.
 active=defaultdict(list)
 for p in parents:
  m=int(p['marketId']);h=str(p['orderHash']);a=int(p['placementCarrierReadyMs']);b=max(a+1,lastMakerFill.get((m,h),int(p['firstFillMs']))+1)
  active[(m,str(p['side']))].append((a,b,h,float(p['targetPrice'])))
 rows=[]
 for p in parents:
  m=int(p['marketId']);side=str(p['side']);t=int(p['placementCarrierReadyMs']);u,dn=inv_before(m,t)
  if abs(u-dn)<=1e-9:role='FLAT';gap=0.
  else:
   weak='UP' if u<dn else 'DOWN';role='MINORITY' if side==weak else 'DOMINANT';gap=abs(u-dn)
  lb=float(p['intentLowerBound']);obs=float(p['observedFillShares']);others=[x for x in active[(m,side)] if x[2]!=str(p['orderHash']) and x[0]<t<x[1]]
  rows.append({'marketId':m,'parentId':p['parentId'],'side':side,'placementCarrierReadyMs':t,'role':role,'strictPastResidual':gap,'intentLowerBound':lb,'observedFillShares':obs,'minimumLegalQty':float(p['minimumLegalQty']),'intentLowerBoundToResidual':lb/gap if gap>1e-9 else None,'observedFillToResidual':obs/gap if gap>1e-9 else None,'existingSameSideActiveParents':len(others),'existingDistinctActivePrices':len({round(x[3],12) for x in others}),'hasExistingSameSideActive':bool(others)})
 minority=[x for x in rows if x['role']=='MINORITY' and x['strictPastResidual']>1e-9];over=[x for x in minority if x['hasExistingSameSideActive']];solo=[x for x in minority if not x['hasExistingSameSideActive']]
 def block(z):
  ratios=[x['intentLowerBoundToResidual'] for x in z];real=[x['observedFillToResidual'] for x in z]
  return {'n':len(z),'markets':len({x['marketId'] for x in z}),'intentLowerBoundToResidual':st(ratios),'observedFillToResidual':st(real),'lowerBoundLt25pctRate':sum(r<.25 for r in ratios)/len(z) if z else None,'lowerBoundLt50pctRate':sum(r<.5 for r in ratios)/len(z) if z else None,'lowerBoundLt75pctRate':sum(r<.75 for r in ratios)/len(z) if z else None,'lowerBoundGe100pctRate':sum(r>=1.0 for r in ratios)/len(z) if z else None,'observedFillLt50pctRate':sum(r<.5 for r in real)/len(z) if z else None,'existingSameSideActiveRate':sum(x['hasExistingSameSideActive'] for x in z)/len(z) if z else None,'existingDistinctActivePrices':st([x['existingDistinctActivePrices'] for x in z])}
 out={'version':'TARGET_ETH_NO18_REPAIR_MAKER_TRANCHE_ANATOMY_V1','researchOnly':True,'actionAuthority':False,'coverage':{'highConfidenceNo18Placements':len(parents),'minorityRepairPlacements':len(minority),'minorityWithExistingSameSideActive':len(over)},'summary':{'ALL_MINORITY':block(minority),'WITH_EXISTING_PASSIVE':block(over),'NO_EXISTING_PASSIVE':block(solo)},'rows':rows,'boundary':['intentLowerBound is no18 reconstruction lower bound, not exact private order size','active interval uses placementCarrierReadyMs through last official Maker fill of same order hash and is retrospective anatomy only','strict-past residual uses wallet fills with event_ms strictly before placementCarrierReadyMs','no V2.1 18-share expected_parent_shares used','no PnL tuning, no runtime constant transfer']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT),'coverage':out['coverage'],'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
