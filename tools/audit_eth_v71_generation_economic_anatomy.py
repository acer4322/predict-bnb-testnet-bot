from __future__ import annotations
import argparse,json,sqlite3,statistics,math
from pathlib import Path
EPS=1e-9
STAGEA=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768]

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return statistics.median(xs) if xs else None

def quant(xs,p):
 xs=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not xs:return None
 if len(xs)==1:return xs[0]
 z=(len(xs)-1)*p;i=int(z);f=z-i
 return xs[i]*(1-f)+xs[min(i+1,len(xs)-1)]*f

def econ(u,d,c):return min(u,d)-c,max(u,d)-c

def target_components(rows,end_by_mid,pre180=True):
 out=[];state={}
 for mid,parent_id,route,side,p,q,t in rows:
  mid=int(mid);t=int(t);p=float(p or 0);q=float(q or 0);side=str(side).upper();route=str(route);parent_id=str(parent_id) if parent_id is not None else None
  if pre180:
   end=end_by_mid.get(mid)
   if end is not None and t>end-180000:continue
  u,d,c=state.get(mid,(0.,0.,0.));gap=abs(u-d);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
  parts=[]
  if weak is None:
   parts=[('EXPAND',q)]
  elif side==weak:
   rq=min(q,gap);xq=max(0.,q-rq)
   if rq>EPS:parts.append(('REPAIR',rq))
   if xq>EPS:parts.append(('EXPAND',xq))
  else:parts=[('EXPAND',q)]
  cu,cd,cc=u,d,c
  for j,(kind,qq) in enumerate(parts):
   f0,b0=econ(cu,cd,cc)
   if side=='UP':cu+=qq
   else:cd+=qq
   cc+=p*qq;f1,b1=econ(cu,cd,cc)
   out.append({'marketId':mid,'t':t,'parentId':parent_id,'route':route,'side':side,'price':p,'qty':qq,'kind':kind,'componentIndex':j,'floorBefore':f0,'floorAfter':f1,'floorDelta':f1-f0,'bestBefore':b0,'bestAfter':b1,'bestDelta':b1-b0,'notional':p*qq})
  state[mid]=(cu,cd,cc)
 return out

def our_components(v70f):
 cand=v70f['rows'][0]['candidate'];ev=sorted(cand.get('v53FillEvents',[]),key=lambda x:(int(x['t']),str(x['key'])))
 u=d=c=0.;out=[]
 for x in ev:
  side=str(x['side']).upper();p=float(x['price']);q=float(x['qty']);role=str(x['role']);kind='REPAIR' if role.endswith('REPAIR') else 'EXPAND' if role.endswith('EXPAND') else None
  if kind is None:continue
  f0,b0=econ(u,d,c)
  if side=='UP':u+=q
  else:d+=q
  c+=p*q;f1,b1=econ(u,d,c)
  out.append({'marketId':1823769,'t':int(x['t']),'key':x['key'],'route':role,'side':side,'price':p,'qty':q,'kind':kind,'floorBefore':f0,'floorAfter':f1,'floorDelta':f1-f0,'bestBefore':b0,'bestAfter':b1,'bestDelta':b1-b0,'notional':p*q})
 return out

def generation_audit(components,relay_only=True):
 by={}
 for x in components:by.setdefault(int(x['marketId']),[]).append(x)
 gens=[];market_rows=[]
 for mid,ev in by.items():
  ev=sorted(ev,key=lambda z:(int(z['t']),0 if z['kind']=='REPAIR' else 1,str(z.get('key',''))))
  open_g=[];repair_seen=False;market_gen=[];debt_curve=[];floor_curve=[]
  for x in ev:
   t=int(x['t']);kind=x['kind'];side=x['side'];q=float(x['qty'])
   if kind=='REPAIR':
    repair_seen=True;rem=q
    for g in open_g:
     if rem<=EPS:break
     if g['paySide']!=side or g['remaining']<=EPS:continue
     pay=min(rem,g['remaining']);frac=pay/q if q>EPS else 0.0
     g['paidQty']+=pay;g['remaining']-=pay;g['repairNotional']+=float(x['notional'])*frac;g['repairFloorRecovery']+=float(x['floorDelta'])*frac;g['repairBestDelta']+=float(x['bestDelta'])*frac
     g['paymentEvents']+=1;g['lastPaymentT']=t
     if g['firstPaymentT'] is None:g['firstPaymentT']=t
     if g['remaining']<=EPS and g['fullPaidAt'] is None:g['fullPaidAt']=t
     rem-=pay
   else:
    is_relay=repair_seen
    if relay_only and not is_relay:continue
    debt_before=sum(max(0.,g['remaining']) for g in open_g)
    matching_before=sum(max(0.,g['remaining']) for g in open_g if g['paySide']==('DOWN' if side=='UP' else 'UP'))
    g={'marketId':mid,'generationIndex':len(market_gen)+1,'t':t,'side':side,'paySide':'DOWN' if side=='UP' else 'UP','route':x.get('route'),'qty':q,'price':float(x['price']),'notional':float(x['notional']),'floorBefore':float(x['floorBefore']),'floorAfterExpand':float(x['floorAfter']),'expandFloorDelta':float(x['floorDelta']),'expandBestDelta':float(x['bestDelta']),'debtBeforeBirth':debt_before,'matchingDebtBeforeBirth':matching_before,'overlapAtBirth':debt_before>EPS,'paidQty':0.0,'remaining':q,'repairNotional':0.0,'repairFloorRecovery':0.0,'repairBestDelta':0.0,'paymentEvents':0,'firstPaymentT':None,'lastPaymentT':None,'fullPaidAt':None,'nextExpandT':None}
    if market_gen:market_gen[-1]['nextExpandT']=t
    market_gen.append(g);open_g.append(g)
   debt_curve.append({'t':t,'outstandingDebt':sum(max(0.,g['remaining']) for g in open_g)})
   floor_curve.append({'t':t,'floor':float(x['floorAfter']),'kind':kind,'side':side})
  for g in market_gen:
   g['fullyPaid']=g['remaining']<=EPS
   g['fullPaidBeforeNextExpand']=bool(g['fullPaidAt'] is not None and (g['nextExpandT'] is None or g['fullPaidAt']<=g['nextExpandT']))
   g['firstPaymentLagSec']=(g['firstPaymentT']-g['t'])/1000. if g['firstPaymentT'] is not None else None
   g['fullPaymentLagSec']=(g['fullPaidAt']-g['t'])/1000. if g['fullPaidAt'] is not None else None
   sacrifice=max(0.,-g['expandFloorDelta']);gain=max(0.,g['expandBestDelta'])
   g['bestGainPerFloorSacrifice']=gain/sacrifice if sacrifice>EPS else None
   g['repairToSacrificeRatio']=g['repairFloorRecovery']/sacrifice if sacrifice>EPS else None
  gens.extend(market_gen)
  market_rows.append({'marketId':mid,'generations':len(market_gen),'overlapBirths':sum(g['overlapAtBirth'] for g in market_gen),'fullyPaidBeforeNextExpand':sum(g['fullPaidBeforeNextExpand'] for g in market_gen),'fullyPaid':sum(g['fullyPaid'] for g in market_gen),'maxOutstandingDebt':max([z['outstandingDebt'] for z in debt_curve]+[0.]),'endOutstandingDebt':debt_curve[-1]['outstandingDebt'] if debt_curve else 0.,'startFloor':floor_curve[0]['floor'] if floor_curve else None,'endFloor':floor_curve[-1]['floor'] if floor_curve else None})
 n=len(gens)
 summary={'markets':len(market_rows),'generations':n,'overlapBirths':sum(g['overlapAtBirth'] for g in gens),'overlapRate':sum(g['overlapAtBirth'] for g in gens)/n if n else None,'fullPaidGenerations':sum(g['fullyPaid'] for g in gens),'fullPaidShare':sum(g['fullyPaid'] for g in gens)/n if n else None,'fullPaidBeforeNextExpand':sum(g['fullPaidBeforeNextExpand'] for g in gens),'fullPaidBeforeNextExpandShare':sum(g['fullPaidBeforeNextExpand'] for g in gens)/n if n else None,'medianDebtBeforeBirth':med([g['debtBeforeBirth'] for g in gens]),'p90DebtBeforeBirth':quant([g['debtBeforeBirth'] for g in gens],.9),'medianExpandFloorDelta':med([g['expandFloorDelta'] for g in gens]),'medianExpandBestDelta':med([g['expandBestDelta'] for g in gens]),'medianBestGainPerFloorSacrifice':med([g['bestGainPerFloorSacrifice'] for g in gens]),'medianRepairToSacrificeRatio':med([g['repairToSacrificeRatio'] for g in gens]),'medianFirstPaymentLagSec':med([g['firstPaymentLagSec'] for g in gens]),'medianFullPaymentLagSec':med([g['fullPaymentLagSec'] for g in gens]),'medianGenerationNotional':med([g['notional'] for g in gens]),'marketRows':market_rows}
 return summary,gens

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target-db',default='data/target_wallet_official_v1.db');ap.add_argument('--v69',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json');ap.add_argument('--v70f',default='data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_20260903.json');ap.add_argument('--output',required=True);a=ap.parse_args()
 v69=json.load(open(a.v69,encoding='utf-8'));end_by={}
 for r in v69['conditionRows']:
  mid=int(r['marketId'])
  if mid not in end_by:
   s=r['ourState'];end_by[mid]=int(round(float(s['snapshotT'])+float(s['remainingSec'])*1000.))
 con=sqlite3.connect(a.target_db);ph=','.join('?'*len(STAGEA));rows=con.execute(f"select market_id,parent_id,role,side,average_price,shares,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",STAGEA).fetchall();con.close()
 tc=target_components(rows,end_by,pre180=True);v70f=json.load(open(a.v70f,encoding='utf-8'));oc=our_components(v70f)
 ts,tg=generation_audit(tc,relay_only=True);os,og=generation_audit(oc,relay_only=True)
 target_overlap=ts['overlapRate'];our_overlap=os['overlapRate'];target_completed=ts['fullPaidBeforeNextExpandShare'];our_completed=os['fullPaidBeforeNextExpandShare']
 materially_lower=(target_overlap is not None and our_overlap is not None and target_overlap+0.15<our_overlap)
 stronger_completion=(target_completed is not None and our_completed is not None and target_completed>our_completed+0.15)
 if materially_lower or stronger_completion:decision='KEEP_GENERATION_COMPLETION_OR_ADMISSION_AS_PRIMARY_MANAGEMENT_AXIS'
 else:decision='DEBT_COMPLETION_ALONE_NOT_SEPARATING_ANALYZE_ECONOMIC_ADMISSION_STATES'
 out={'version':'ETH_REPAIR_V71_GENERATION_ECONOMIC_ANATOMY','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'scope':{'our':'V70F market 1823769 realistic-HFT actual fills','target':'official Target ETH Stage-A16 actual parent fills, only pre-180 relay generations after a prior Repair component'},'target':ts,'ourV70F':os,'comparison':{'targetOverlapRate':target_overlap,'ourOverlapRate':our_overlap,'targetFullPaidBeforeNextExpandShare':target_completed,'ourFullPaidBeforeNextExpandShare':our_completed,'targetMinusOurOverlap':(target_overlap-our_overlap) if target_overlap is not None and our_overlap is not None else None,'targetMinusOurCompletionShare':(target_completed-our_completed) if target_completed is not None and our_completed is not None else None},'gates':{'targetUsesOwnOfficialInventory':True,'ourUsesActualRealisticHFTFillTrajectory':True,'strictlyPostBirthRepairPayment':True,'materiallyLowerTargetOverlap':materially_lower,'materiallyStrongerTargetCompletionBeforeNextExpand':stronger_completion},'decision':decision,'targetGenerations':tg,'ourGenerations':og,'boundary':['descriptive/diagnostic only','no Target future action or winner runtime authority','no threshold/qty/delay tuning','no H100','no 8781']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'decision':decision,'comparison':out['comparison'],'target':{k:ts[k] for k in ts if k!='marketRows'},'our':{k:os[k] for k in os if k!='marketRows'}},ensure_ascii=False))
if __name__=='__main__':main()
