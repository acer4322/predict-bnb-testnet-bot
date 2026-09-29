from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9
FIXED=[1824037,1825353]
def floor(u,d,c):return min(u,d)-c
def audit_market(r):
 ev=sorted(r['functional'].get('v53FillEvents',[]),key=lambda x:(int(x['t']),str(x['key'])))
 u=d=c=0.0;states=[]
 for x in ev:
  before=floor(u,d,c);q=float(x['qty']);p=float(x['price']);side=x['side'];
  if side=='UP':u+=q
  else:d+=q
  c+=q*p;after=floor(u,d,c);states.append({**x,'floorBefore':before,'floorAfter':after})
 active=[(i,x) for i,x in enumerate(states) if x.get('role')=='ACTIVE_EXPAND']
 if not active:return {'marketId':r['marketId'],'error':'NO_ACTIVE_EXPAND_FILL'}
 i,a=active[0];repair='DOWN' if a['side']=='UP' else 'UP';post=[]
 for z in states[i+1:]:
  if z.get('role') in ('PASSIVE_EXPAND','ACTIVE_EXPAND'):break
  if z.get('role') in ('PASSIVE_REPAIR','ACTIVE_REPAIR') and z.get('side')==repair:post.append(z)
 need=float(a['qty']);left=need;matched=[];mq=mn=0.0
 for z in post:
  take=min(left,float(z['qty']));
  if take<=EPS:continue
  mq+=take;mn+=take*float(z['price']);matched.append({'key':z['key'],'t':z['t'],'qty':z['qty'],'matchedQty':take,'price':z['price'],'floorAfter':z['floorAfter']});left-=take
  if left<=EPS:break
 weighted=mn/mq if mq>EPS else None;pair=float(a['price'])+weighted if weighted is not None else None;allq=sum(float(z['qty']) for z in post);window_floor=post[-1]['floorAfter'] if post else a['floorAfter'];delta=window_floor-float(a['floorBefore'])
 return {'marketId':r['marketId'],'activeKey':a['key'],'activeT':a['t'],'activeSide':a['side'],'activeQty':need,'activePrice':a['price'],'floorBeforeActive':a['floorBefore'],'floorAfterActive':a['floorAfter'],'oppositeRepairFillCount':len(post),'oppositeRepairQtyTotal':allq,'matchedRepairQty':mq,'unmatchedActiveQty':max(0.0,left),'matchedRepairPrice':weighted,'forwardPairSum':pair,'matchedPairNonDamaging':bool(pair is not None and pair<=1+EPS),'floorAfterSettlementWindow':window_floor,'floorDeltaActiveThroughSettlement':delta,'settlementImprovesVsPreActive':delta>EPS,'matchedRepairRows':matched,'allRepairRows':[{k:z[k] for k in ('key','t','qty','price','floorAfter')} for z in post]}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--v73c',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();d=json.load(open(a.v73c,encoding='utf-8'));by={int(r['marketId']):r for r in d['rows']};rows=[audit_market(by[m]) for m in FIXED]
 good=[x for x in rows if 'error' not in x and x['matchedRepairQty']>EPS and (x['matchedPairNonDamaging'] or x['settlementImprovesVsPreActive'])]
 decision='KEEP_RECOVERABILITY_VALIDATION' if len(good)==len(FIXED) else 'RECOVERABILITY_CONTEXT_MISMATCH_AUDIT_STATE'
 out={'version':'ETH_REPAIR_V73E_STAGEA_NEW_CONTEXT_FORWARD_SETTLEMENT','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'fixedMarkets':FIXED,'rows':rows,'decision':decision,'gates':{'bothActiveFallbacksMaterialized':all('error' not in x for x in rows),'bothHaveOppositeRepairSettlement':all(x.get('matchedRepairQty',0)>EPS for x in rows),'bothConsistentWithRecoverability':len(good)==2},'boundary':['post-episode audit only','realistic-HFT actual fills','no runtime future input','no action mutation','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
