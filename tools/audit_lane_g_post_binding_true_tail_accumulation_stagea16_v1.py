from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import Counter,defaultdict
EPS=1e-9; ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();p=json.loads(Path(a.source).read_text(encoding='utf-8'))
 rows=[r for r in p.get('rows',[]) if r.get('cell')=='MS4_R239_OVERFLOW_RESPONSIBILITY_HANDOFF'];outrows=[]
 for r in rows:
  mid=int(r['marketId']);splits=list(r.get('splitEvents') or []);revs=list(r.get('r239Events') or []);obs=list(r.get('r239Obligations') or [])
  dedicatedKeys=set();dedicated=defaultdict(list)
  for e in revs:
   if e.get('event')=='R239_HANDOFF_REPAIR_FILL':dedicated[int(e['obligationId'])].append(e);dedicatedKeys.add(str(e.get('key')))
  for ob in obs:
   oid=int(ob['id']);side=str(ob['side']);gen=int(ob['generation']);born=int(ob['bornT']);closed=int(ob.get('closedT') or 10**30);remaining=float(ob.get('originOverflowQty') or 0.0);timeline=[]
   for e in dedicated.get(oid,[]):timeline.append((int(e.get('t') or 0),0,str(e.get('key')),float(e.get('liabilityPaidQty') or 0.0),'DEDICATED'))
   rs='DOWN' if side=='UP' else 'UP'
   for e in splits:
    if e.get('event')!='ROLE_FILL_SPLIT' or str(e.get('role')) not in ROLES:continue
    t=int(e.get('t') or -1);key=str(e.get('key'));eg=int(e.get('generationAtSubmit') or e.get('generation') or -1);rq=float(e.get('repairAllocated') or 0.0)
    if born<=t<=closed and key not in dedicatedKeys and str(e.get('side'))==rs and eg==gen and rq>EPS:timeline.append((t,1,key,rq,str(e.get('role'))))
   timeline.sort();payments=[]
   for t,kind,key,qty,role in timeline:
    paid=min(remaining,qty);remaining=max(0.0,remaining-paid)
    if paid>EPS:payments.append({'t':t,'key':key,'kind':'DEDICATED' if kind==0 else 'GENERIC','role':role,'availableRepairQty':qty,'ownerPaymentQty':paid,'remainingAfter':remaining})
   outrows.append({'marketId':mid,'obligationId':oid,'side':side,'generation':gen,'originOverflowQty':float(ob.get('originOverflowQty') or 0.0),'recordedOutstanding':float(ob.get('outstanding') or 0.0),'recordedCloseReason':ob.get('closeReason'),'simulatedOutstandingAfterValidatedPayments':remaining,'validatedPaymentQty':float(ob.get('originOverflowQty') or 0.0)-remaining,'paymentEvents':payments})
 residual=[x for x in outrows if x['simulatedOutstandingAfterValidatedPayments']>EPS];by=Counter(x['marketId'] for x in residual);multi={str(k):v for k,v in by.items() if v>1};knownTrue=[x for x in residual if (x['marketId'],x['obligationId']) in {(1945869,1),(1946872,1)}]
 out={'version':'LANE_G_POST_BINDING_TRUE_TAIL_ACCUMULATION_STAGEA16_AUDIT_V1_RESULT_20260908','researchOnly':True,'behaviorMutation':False,'source':a.source,'totalObligations':len(outrows),'residualObligationsAfterValidatedPayments':len(residual),'residualMarketCount':len(by),'residualQtySum':sum(x['simulatedOutstandingAfterValidatedPayments'] for x in residual),'residualQtyMax':max([x['simulatedOutstandingAfterValidatedPayments'] for x in residual] or [0.0]),'marketsWithMultipleResidualOwners':multi,'knownRouteAuditedTrueTails':knownTrue,'knownTrueTailQtySum':sum(x['simulatedOutstandingAfterValidatedPayments'] for x in knownTrue),'knownTrueTailQtyMax':max([x['simulatedOutstandingAfterValidatedPayments'] for x in knownTrue] or [0.0]),'rows':outrows,'boundary':['read-only consumed Stage-A16 artifact','validated dedicated + generic confirmed Repair-role payments only','no behavior mutation','no fresh','no dream fill','no 8781','no time/window/rank-age gate']}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'totalObligations':out['totalObligations'],'residualObligations':out['residualObligationsAfterValidatedPayments'],'residualMarkets':out['residualMarketCount'],'residualQtySum':out['residualQtySum'],'residualQtyMax':out['residualQtyMax'],'multipleResidualOwners':out['marketsWithMultipleResidualOwners'],'knownTrueTailQtySum':out['knownTrueTailQtySum']},ensure_ascii=False))
if __name__=='__main__':main()
