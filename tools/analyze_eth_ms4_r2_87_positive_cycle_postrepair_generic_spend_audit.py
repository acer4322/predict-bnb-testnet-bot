from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--r264',required=True);ap.add_argument('--r285',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    r264=json.load(open(a.r264,encoding='utf-8'));r285=json.load(open(a.r285,encoding='utf-8'))
    base={int(r['marketId']):r for r in r264['rows'] if r.get('cell')=='R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND'}
    outrows=[]
    for mr in r285['rows']:
        mid=int(mr['marketId']);b=base[mid]
        risk_keys={str(x.get('key')) for x in (b.get('riskTrancheLedger') or [])}
        splits=[x for x in (b.get('splitEvents') or []) if x.get('event')=='ROLE_FILL_SPLIT']
        for z in mr.get('tranches') or []:
            if z.get('completedAt') is None or float(z.get('pairEdge') or 0.0)<-EPS:continue
            if float(z.get('passivePaidQty') or 0.0)<float(z.get('qty') or 0.0)-EPS or float(z.get('activePaidQty') or 0.0)>EPS:continue
            pays=z.get('payments') or []
            first=min(int(p['t']) for p in pays) if pays else int(z['completedAt'])
            end=int(z['completedAt']);gen=int(z['generation'])
            generic=[];explicit=[]
            for e in splits:
                if str(e.get('role'))!='SATELLITE_EXPAND':continue
                t=int(e.get('t') or 0)
                if t<first or t>end:continue
                if int(e.get('generationAtSubmit') or -1)!=gen:continue
                risk=float(e.get('overflowRisk') or 0.0)
                row={'t':t,'key':str(e.get('key')),'price':float(e.get('price') or 0.0),'fillQty':float(e.get('fillInc') or 0.0),'riskSpend':risk}
                (explicit if row['key'] in risk_keys else generic).append(row)
            x={'marketId':mid,'trancheId':int(z['id'] if 'id' in z else z.get('trancheId')),'key':z['key'],'generation':gen,
               'entryPrice':float(z['entryPrice']),'qty':float(z['qty']),'riskPrincipal':float(z['qty'])*float(z['entryPrice']),
               'firstRepairAt':first,'completedAt':end,'pairEdge':float(z['pairEdge']),
               'genericExpandRiskSpendAfterRepair':sum(r['riskSpend'] for r in generic),'genericExpandFillsAfterRepair':len(generic),
               'explicitRiskSpendAfterRepair':sum(r['riskSpend'] for r in explicit),'genericRows':generic,'explicitRows':explicit,
               'fullPrincipalTransferClean':sum(r['riskSpend'] for r in generic)<=EPS}
            outrows.append(x);print(json.dumps({k:x[k] for k in ['marketId','trancheId','key','riskPrincipal','pairEdge','genericExpandFillsAfterRepair','genericExpandRiskSpendAfterRepair','fullPrincipalTransferClean']},ensure_ascii=False),flush=True)
    s={'positivePassiveCycles':len(outrows),'cleanFullPrincipalCycles':sum(x['fullPrincipalTransferClean'] for x in outrows),
       'cyclesWithGenericSpendAfterRepair':sum(not x['fullPrincipalTransferClean'] for x in outrows),
       'genericRiskSpendAfterRepair':sum(x['genericExpandRiskSpendAfterRepair'] for x in outrows),
       'markets':len(set(x['marketId'] for x in outrows))}
    out={'version':'MS4_R2_87_POSITIVE_CYCLE_POSTREPAIR_GENERIC_SPEND_AUDIT_V1','researchOnly':True,'behaviorChange':False,
         'sourceR264':a.r264,'sourceR285':a.r285,'summary':s,'rows':outrows,
         'boundary':['strict chronology from R2.85','positive passive-only completed cycles only','generic spend means SATELLITE_EXPAND fill after first Repair payment and by completion, excluding explicit riskTranche keys','no winner/action use','behavior inert']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
