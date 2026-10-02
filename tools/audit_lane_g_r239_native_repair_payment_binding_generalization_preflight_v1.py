from __future__ import annotations
import argparse,json
from pathlib import Path
EPS=1e-9

def opp(s): return 'DOWN' if str(s)=='UP' else 'UP'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.input).read_text(encoding='utf-8'))
    rows=[r for r in d.get('rows',[]) if str(r.get('cell'))=='MS4_R239_OVERFLOW_RESPONSIBILITY_HANDOFF']
    findings=[]; total_obs=0
    for r in rows:
        mid=int(r['marketId']);splits=r.get('splitEvents') or []; events=r.get('r239Events') or []; obs=r.get('r239Obligations') or []
        dedicated={(int(e.get('t') or -1),str(e.get('key'))) for e in events if e.get('event')=='R239_HANDOFF_REPAIR_FILL'}
        closes={int(e.get('obligationId')):e for e in events if e.get('event')=='R239_OVERFLOW_OBLIGATION_CLOSED'}
        for ob in obs:
            total_obs+=1; oid=int(ob.get('id') or -1); born=int(ob.get('bornT') or -1); side=str(ob.get('side')); gen=int(ob.get('generation') or -1); close=closes.get(oid); close_t=int(close.get('t')) if close else 10**30
            origin=float(ob.get('originOverflowQty') or 0.0); recorded=float(ob.get('repaidQty') or 0.0)
            # Conservative available amount for detecting missed native payment; dedicated payments already excluded below.
            remaining=max(0.0,origin-recorded)
            cands=[]
            for e in splits:
                t=int(e.get('t') or -1); key=str(e.get('key'))
                if not (born < t <= close_t): continue
                if (t,key) in dedicated: continue
                if e.get('event')!='ROLE_FILL_SPLIT' or str(e.get('role'))!='ECONOMIC_CORE':continue
                if int(e.get('generationAtSubmit') or -1)!=gen or str(e.get('side'))!=opp(side):continue
                rq=float(e.get('repairAllocated') or 0.0)
                if rq<=EPS:continue
                pay=min(max(0.0,remaining),rq)
                cands.append({'t':t,'key':key,'side':e.get('side'),'generation':gen,'price':float(e.get('price') or 0.0),'fillInc':float(e.get('fillInc') or 0.0),'repairAllocated':rq,'overflowRealized':float(e.get('overflowRealized') or 0.0),'potentialOwnerPayment':pay})
                remaining=max(0.0,remaining-pay)
            if cands:
                findings.append({'marketId':mid,'obligationId':oid,'side':side,'generation':gen,'bornT':born,'originOverflowQty':origin,'recordedRepaidQty':recorded,'finalOutstanding':float(ob.get('outstanding') or 0.0),'closeReason':ob.get('closeReason'),'candidateNativeRepairReceipts':cands,'potentialNativePaymentQty':sum(x['potentialOwnerPayment'] for x in cands),'wouldFullyCoverOrigin':sum(x['potentialOwnerPayment'] for x in cands)+recorded>=origin-EPS})
    affected=sorted({x['marketId'] for x in findings})
    full=sum(1 for x in findings if x['wouldFullyCoverOrigin'])
    out={'version':'LANE_G_R239_NATIVE_REPAIR_PAYMENT_BINDING_GENERALIZATION_PREFLIGHT_V1_RESULT_20260907','researchOnly':True,'behaviorMutation':False,'source':a.input,'candidateRows':len(rows),'totalR239Obligations':total_obs,'obligationsWithNativeRepairCandidate':len(findings),'affectedMarkets':affected,'affectedMarketCount':len(affected),'fullyCoverableObligations':full,'findings':findings,'classification':'STRUCTURAL_PAYMENT_BINDING_GAP_REPEATS' if len(affected)>=2 else ('SINGLE_MARKET_ONLY' if affected else 'NO_GENERALIZATION_SUPPORT'),'boundary':['read-only consumed Stage-A16 artifact','no replay','dedicated R239 handoff fills excluded','no policy/authority/credit mutation','no fresh','no dream fill','no 8781']}
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'classification':out['classification'],'totalObligations':total_obs,'withNativeCandidate':len(findings),'affectedMarkets':affected,'fullyCoverable':full},ensure_ascii=False))
if __name__=='__main__':main()
