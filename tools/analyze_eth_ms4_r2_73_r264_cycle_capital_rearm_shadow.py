from __future__ import annotations
import argparse,json,math
from pathlib import Path
from collections import defaultdict,deque,Counter
EPS=1e-9

def analyze_row(r):
    meta={str(x['key']):x for x in (r.get('riskTrancheLedger') or [])}
    split=[x for x in (r.get('splitEvents') or []) if x.get('event')=='ROLE_FILL_SPLIT']
    # price/side lookup for actual Repair payment keys.
    repair_lookup={(int(x.get('t') or 0),str(x.get('key'))):x for x in split if float(x.get('repairAllocated') or 0.0)>EPS}
    tranches=[]; by_gen=defaultdict(deque)
    for x in sorted(split,key=lambda z:(int(z.get('t') or 0),str(z.get('key')))):
        key=str(x.get('key')); inc=float(x.get('fillInc') or 0.0)
        if key not in meta or inc<=EPS:continue
        m=meta[key]; gen=int(m.get('generation') or x.get('generationAtSubmit') or -1)
        z={'id':len(tranches)+1,'key':key,'generation':gen,'createdAt':int(x.get('t') or 0),
           'side':str(x.get('side')),'entryPrice':float(x.get('price') or m.get('price') or 0.0),
           'qty':inc,'remaining':inc,'paidQty':0.0,'passivePaidQty':0.0,'activePaidQty':0.0,
           'pairEdge':0.0,'completedAt':None,'payments':[]}
        tranches.append(z);by_gen[gen].append(z)
    orphan_pay=0.0;pay_events=0
    pays=[x for x in (r.get('r257Events') or []) if x.get('event')=='R257_RISK_REPAIR_OBLIGATION_PAYMENT']
    for e in sorted(pays,key=lambda z:(int(z.get('t') or 0),str(z.get('key')))):
        q=float(e.get('paid') or 0.0)
        if q<=EPS:continue
        pay_events+=1;gen=int(e.get('generation') or -1);lk=repair_lookup.get((int(e.get('t') or 0),str(e.get('key'))),{})
        price=float(lk.get('price') or 0.0);active=bool(e.get('active'))
        dq=by_gen[gen]
        while q>EPS and dq:
            z=dq[0]
            if z['remaining']<=EPS:
                dq.popleft();continue
            take=min(q,z['remaining']);z['remaining']-=take;z['paidQty']+=take
            if active:z['activePaidQty']+=take
            else:z['passivePaidQty']+=take
            edge=take*(1.0-z['entryPrice']-price);z['pairEdge']+=edge
            z['payments'].append({'t':int(e.get('t') or 0),'key':str(e.get('key')),'price':price,'qty':take,'active':active,'edge':edge})
            q-=take
            if z['remaining']<=EPS:
                z['remaining']=0.0;z['completedAt']=int(e.get('t') or 0);dq.popleft()
        if q>EPS:orphan_pay+=q
    completed=[z for z in tranches if z['completedAt'] is not None]
    nonneg=[z for z in completed if z['pairEdge']>=-EPS]
    passive_only=[z for z in nonneg if z['passivePaidQty']>=z['qty']-EPS and z['activePaidQty']<=EPS]
    passive_any=[z for z in nonneg if z['passivePaidQty']>EPS]
    neg=[z for z in completed if z['pairEdge']<-EPS]
    return {'marketId':int(r['marketId']),'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),
            'fills':int(r['fillEvents']),'riskFillTranches':len(tranches),'completedTranches':len(completed),
            'nonnegativeCompleted':len(nonneg),'negativeCompleted':len(neg),'passiveOnlyNonnegative':len(passive_only),
            'passiveAnyNonnegative':len(passive_any),'completedPairEdge':sum(z['pairEdge'] for z in completed),
            'nonnegativePairEdge':sum(z['pairEdge'] for z in nonneg),'negativePairEdge':sum(z['pairEdge'] for z in neg),
            'unrepaidQty':sum(z['remaining'] for z in tranches),'orphanPaymentQty':orphan_pay,'paymentEvents':pay_events,
            'tranches':tranches}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--r264',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.load(open(a.r264,encoding='utf-8'));rows=[]
    for r in d['rows']:
        if r.get('cell')!='R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND':continue
        x=analyze_row(r);rows.append(x)
        print(json.dumps({k:x[k] for k in ['marketId','pnl','best','floor','riskFillTranches','completedTranches','nonnegativeCompleted','negativeCompleted','passiveOnlyNonnegative','completedPairEdge','unrepaidQty']},ensure_ascii=False),flush=True)
    agg=Counter()
    for x in rows:
        for k in ['riskFillTranches','completedTranches','nonnegativeCompleted','negativeCompleted','passiveOnlyNonnegative','passiveAnyNonnegative','paymentEvents']:
            agg[k]+=int(x[k])
    summary={'markets':len(rows),**dict(agg),'completedPairEdge':sum(x['completedPairEdge'] for x in rows),
             'negativePairEdge':sum(x['negativePairEdge'] for x in rows),'nonnegativePairEdge':sum(x['nonnegativePairEdge'] for x in rows),
             'marketsWithPassiveOnlyNonnegativeToken':sum(x['passiveOnlyNonnegative']>0 for x in rows),
             'marketsWithAnyNonnegativeToken':sum(x['nonnegativeCompleted']>0 for x in rows),
             'marketsBestGt2':sum(x['best']>2 for x in rows),'marketsFloorGtMinus1':sum(x['floor']>-1 for x in rows)}
    out={'version':'MS4_R2_73_R264_CYCLE_CAPITAL_REARM_SHADOW_V1','researchOnly':True,'behaviorChange':False,
         'source':a.r264,'summary':summary,'rows':rows,
         'interpretationBoundary':['post-hoc shadow over OUR actual fills only','risk tranches are confirmed risk-key fill increments','Repair pays risk tranches FIFO within generation','pair edge uses actual risk entry and actual Repair payment price','nonnegative completion is candidate capital-replenishment evidence, not runtime authority yet','passive-only completion tracked separately','winner only used for diagnostic PnL already present in source','no behavior change']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
