from __future__ import annotations
import argparse,json,statistics
from pathlib import Path
EPS=1e-9

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--r285',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.load(open(a.r285,encoding='utf-8'));rows=[]
    for mr in d['rows']:
        mid=int(mr['marketId'])
        for z in mr.get('tranches') or []:
            qty=float(z.get('qty') or 0.0);ep=float(z.get('entryPrice') or 0.0);principal=qty*ep
            deficit=principal;used=0.0;passive_used=0.0;active_used=0.0;overrepair=0.0;steps=[];recovered_at=None
            for p in z.get('payments') or []:
                pq=float(p.get('qty') or 0.0);rp=float(p.get('price') or 0.0);unit=max(0.0,1.0-rp)
                need=deficit/unit if unit>EPS else 1e99
                take=min(pq,max(0.0,need)) if deficit>EPS else 0.0
                rec=take*unit;deficit=max(0.0,deficit-rec);used+=take
                if bool(p.get('active')):active_used+=take
                else:passive_used+=take
                excess=max(0.0,pq-take);overrepair+=excess
                steps.append({'t':int(p.get('t') or 0),'key':p.get('key'),'price':rp,'actualQty':pq,'payoffNeededQty':take,'capitalRecovered':rec,'deficitAfter':deficit,'active':bool(p.get('active')),'counterfactualOverRepairQty':excess})
                if deficit<=EPS and recovered_at is None:recovered_at=int(p.get('t') or 0)
            recoverable=deficit<=EPS
            residual=max(0.0,qty-used) if recoverable else 0.0
            residual_frac=residual/qty if qty>EPS else 0.0
            actual_paid=float(z.get('paidQty') or 0.0)
            x={'marketId':mid,'trancheId':int(z.get('id') if 'id' in z else z.get('trancheId')),'key':z.get('key'),'generation':int(z.get('generation') or -1),
               'side':z.get('side'),'entryPrice':ep,'riskQty':qty,'riskPrincipal':principal,'actualPaidQty':actual_paid,
               'actualPassivePaidQty':float(z.get('passivePaidQty') or 0.0),'actualActivePaidQty':float(z.get('activePaidQty') or 0.0),
               'actualPairEdge':float(z.get('pairEdge') or 0.0),'payoffRecoverable':recoverable,'payoffDeficitRemaining':deficit,
               'payoffRepairQtyNeeded':used,'payoffPassiveQtyUsed':passive_used,'payoffActiveQtyUsed':active_used,
               'directionalResidualQty':residual,'directionalResidualFraction':residual_frac,
               'counterfactualOverRepairQty':max(0.0,actual_paid-used),'capitalRecoveredAt':recovered_at,'steps':steps}
            rows.append(x);print(json.dumps({k:x[k] for k in ['marketId','trancheId','key','entryPrice','riskQty','riskPrincipal','actualPaidQty','payoffRecoverable','payoffDeficitRemaining','payoffRepairQtyNeeded','directionalResidualQty','directionalResidualFraction','counterfactualOverRepairQty','payoffActiveQtyUsed']},ensure_ascii=False),flush=True)
    rec=[x for x in rows if x['payoffRecoverable']];notrec=[x for x in rows if not x['payoffRecoverable']]
    s={'tranches':len(rows),'payoffRecoverable':len(rec),'payoffUnrecoverable':len(notrec),
       'recoverableWithPassiveOnly':sum(x['payoffRecoverable'] and x['payoffActiveQtyUsed']<=EPS for x in rows),
       'recoverableNeedsActive':sum(x['payoffRecoverable'] and x['payoffActiveQtyUsed']>EPS for x in rows),
       'totalActualPaidQty':sum(x['actualPaidQty'] for x in rows),'totalPayoffRepairQtyNeeded':sum(x['payoffRepairQtyNeeded'] for x in rows),
       'totalCounterfactualOverRepairQty':sum(x['counterfactualOverRepairQty'] for x in rows),
       'totalDirectionalResidualQty':sum(x['directionalResidualQty'] for x in rows),
       'medianDirectionalResidualFractionRecoverable':statistics.median([x['directionalResidualFraction'] for x in rec]) if rec else None,
       'marketsWithRecoverableResidual':len(set(x['marketId'] for x in rec if x['directionalResidualQty']>EPS)),
       'markets':len(set(x['marketId'] for x in rows))}
    out={'version':'MS4_R2_89_PAYOFF_CAPITAL_RESPONSIBILITY_SHADOW_V1','researchOnly':True,'behaviorChange':False,'source':a.r285,'summary':s,'rows':rows,
         'boundary':['strict chronology from R2.85 actual risk/Repair payments','risk capital deficit = riskQty*entryPrice','each actual Repair qty recovers qty*(1-repairPrice) until capital deficit zero','Passive and Active both pay capital responsibility','quantity after capital recovery is counterfactual over-repair and desired-direction residual candidate','does not model changed future execution after earlier stop','winner unused','no behavior change']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
