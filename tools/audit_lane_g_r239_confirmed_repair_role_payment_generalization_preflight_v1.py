from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9
ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    p=json.loads(Path(a.source).read_text(encoding='utf-8'))
    rows=[r for r in p.get('rows',[]) if r.get('cell')=='MS4_R239_OVERFLOW_RESPONSIBILITY_HANDOFF']
    findings=[];total_obs=0
    for r in rows:
        mid=int(r['marketId']);splits=list(r.get('splitEvents') or []);revs=list(r.get('r239Events') or []);obs=list(r.get('r239Obligations') or [])
        dedicated_by_oid={}
        dedicated_keys=set()
        for e in revs:
            if e.get('event')=='R239_HANDOFF_REPAIR_FILL':
                oid=int(e.get('obligationId'));dedicated_by_oid.setdefault(oid,[]).append(e);dedicated_keys.add(str(e.get('key')))
        for ob in obs:
            total_obs+=1;oid=int(ob['id']);side=str(ob['side']);gen=int(ob['generation']);born=int(ob['bornT']);closed=int(ob.get('closedT') or 10**30)
            remaining=float(ob.get('originOverflowQty') or 0.0);timeline=[]
            for e in dedicated_by_oid.get(oid,[]):
                timeline.append((int(e.get('t') or 0),0,{'kind':'DEDICATED','key':str(e.get('key')),'qty':float(e.get('liabilityPaidQty') or 0.0)}))
            repair_side='DOWN' if side=='UP' else 'UP'
            for e in splits:
                if e.get('event')!='ROLE_FILL_SPLIT' or str(e.get('role')) not in ROLES:continue
                t=int(e.get('t') or -1);key=str(e.get('key'));evgen=int(e.get('generationAtSubmit') or e.get('generation') or -1)
                if not (born<=t<=closed) or key in dedicated_keys or str(e.get('side'))!=repair_side or evgen!=gen:continue
                rq=float(e.get('repairAllocated') or 0.0)
                if rq<=EPS:continue
                timeline.append((t,1,{'kind':'GENERIC','key':key,'role':str(e.get('role')),'repairAllocated':rq,'overflowRealized':float(e.get('overflowRealized') or 0.0)}))
            timeline.sort(key=lambda x:(x[0],x[1],x[2]['key']))
            generic=[]
            for t,_,e in timeline:
                if e['kind']=='DEDICATED':remaining=max(0.0,remaining-e['qty']);continue
                paid=min(remaining,e['repairAllocated'])
                if paid>EPS:
                    generic.append({'t':t,'key':e['key'],'role':e['role'],'repairAllocated':e['repairAllocated'],'overflowRealized':e['overflowRealized'],'potentialOwnerPayment':paid,'remainingBefore':remaining,'remainingAfter':max(0.0,remaining-paid)})
                    remaining=max(0.0,remaining-paid)
            if generic:
                findings.append({'marketId':mid,'obligationId':oid,'side':side,'generation':gen,'originOverflowQty':float(ob.get('originOverflowQty') or 0.0),'recordedRepaidQty':float(ob.get('repaidQty') or 0.0),'recordedFinalOutstanding':float(ob.get('outstanding') or 0.0),'closeReason':ob.get('closeReason'),'genericConfirmedRepairPayments':generic,'genericPaymentQty':sum(x['potentialOwnerPayment'] for x in generic),'simulatedOutstandingAfterAllKnownRepairPayments':remaining,'wouldFullyRepay':remaining<=EPS})
    out={'version':'LANE_G_R239_CONFIRMED_REPAIR_ROLE_PAYMENT_GENERALIZATION_PREFLIGHT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'source':a.source,'candidateRows':len(rows),'totalR239Obligations':total_obs,'obligationsWithGenericConfirmedRepairPayment':len(findings),'affectedMarkets':sorted({x['marketId'] for x in findings}),'affectedMarketCount':len({x['marketId'] for x in findings}),'fullyRepaidByKnownPayments':sum(bool(x['wouldFullyRepay']) for x in findings),'roleCounts':{role:sum(1 for x in findings for e in x['genericConfirmedRepairPayments'] if e['role']==role) for role in sorted(ROLES)},'findings':findings,'classification':'STRUCTURAL_CONFIRMED_REPAIR_ROLE_PAYMENT_GAP_REPEATS' if findings else 'NO_ADDITIONAL_GAP_FOUND','boundary':['read-only consumed Stage-A16 artifact','dedicated R239 handoff keys excluded','same-generation opposite-side confirmed Repair-role fills only','no replay mutation','no fresh','no dream fill','no 8781']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'classification':out['classification'],'affectedMarkets':out['affectedMarkets'],'obligations':len(findings),'fullyRepaid':out['fullyRepaidByKnownPayments'],'roleCounts':out['roleCounts']},ensure_ascii=False))
if __name__=='__main__':main()
