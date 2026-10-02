from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import defaultdict,deque,Counter
EPS=1e-9

def analyze_row(r):
    # Token-funded thesis rearm fills only.
    r278fills=[x for x in (r.get('r278Events') or []) if x.get('event')=='R278_INTENT_THESIS_CYCLE_REARM_FILL']
    risk={str(x['key']):{'key':str(x['key']),'generation':int(x.get('generation') or -1),'side':str(x.get('side')),
                        'entryPrice':float(x.get('price') or 0.0),'qty':float(x.get('fillQty') or 0.0),'createdAt':int(x.get('t') or 0)} for x in r278fills}
    split=[x for x in (r.get('splitEvents') or []) if x.get('event')=='ROLE_FILL_SPLIT']
    # Split may include rearm fills more accurately; keep FIFO creation order.
    tr=[];bygen=defaultdict(deque)
    for x in sorted(split,key=lambda z:(int(z.get('t') or 0),str(z.get('key')))):
        k=str(x.get('key'));inc=float(x.get('fillInc') or 0.0)
        if k not in risk or inc<=EPS:continue
        q=min(inc,float(risk[k]['qty']))
        z={**risk[k],'qty':q,'remaining':q,'withinCeilingQty':0.0,'aboveCeilingQty':0.0,
           'withinEdge':0.0,'aboveEdge':0.0,'payments':[],'completedAt':None}
        tr.append(z);bygen[z['generation']].append(z)
    # Use actual R257 obligation payments; price from same-clock split.
    lookup={(int(x.get('t') or 0),str(x.get('key'))):x for x in split if float(x.get('repairAllocated') or 0.0)>EPS}
    for e in sorted([x for x in (r.get('r257Events') or []) if x.get('event')=='R257_RISK_REPAIR_OBLIGATION_PAYMENT'],key=lambda z:(int(z.get('t') or 0),str(z.get('key')))):
        q=float(e.get('paid') or 0.0);gen=int(e.get('generation') or -1)
        if q<=EPS or not bygen.get(gen):continue
        lk=lookup.get((int(e.get('t') or 0),str(e.get('key'))),{});px=float(lk.get('price') or 0.0);active=bool(e.get('active'))
        dq=bygen[gen]
        while q>EPS and dq:
            z=dq[0]
            if z['remaining']<=EPS:dq.popleft();continue
            take=min(q,z['remaining']);q-=take;z['remaining']-=take
            ceiling=1.0-z['entryPrice'];edge=take*(ceiling-px)
            within=px<=ceiling+EPS
            if within:z['withinCeilingQty']+=take;z['withinEdge']+=edge
            else:z['aboveCeilingQty']+=take;z['aboveEdge']+=edge
            z['payments'].append({'t':int(e.get('t') or 0),'key':str(e.get('key')),'price':px,'qty':take,'active':active,
                                  'ceiling':ceiling,'withinCeiling':within,'edge':edge})
            if z['remaining']<=EPS:z['remaining']=0.0;z['completedAt']=int(e.get('t') or 0);dq.popleft()
    for z in tr:
        z['totalEdge']=z['withinEdge']+z['aboveEdge'];z['paidQty']=z['qty']-z['remaining']
        z['aboveShareOfPaid']=(z['aboveCeilingQty']/z['paidQty'] if z['paidQty']>EPS else None)
    completed=[z for z in tr if z['completedAt'] is not None]
    return {'marketId':int(r['marketId']),'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),
            'tranches':tr,'summary':{'tranches':len(tr),'completed':len(completed),'withinCeilingQty':sum(z['withinCeilingQty'] for z in tr),
             'aboveCeilingQty':sum(z['aboveCeilingQty'] for z in tr),'withinEdge':sum(z['withinEdge'] for z in tr),
             'aboveEdge':sum(z['aboveEdge'] for z in tr),'totalEdge':sum(z['totalEdge'] for z in tr),
             'negativeCompleted':sum(z['completedAt'] is not None and z['totalEdge']<-EPS for z in tr)}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--result',required=True);ap.add_argument('--cell',default='R282_POST_REARM_FILL_FAVORABLE_REPAIR_ANCHOR');ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.load(open(a.result,encoding='utf-8'));rows=[]
    for r in d.get('rows',[]):
        if r.get('cell')!=a.cell:continue
        x=analyze_row(r);rows.append(x);print(json.dumps({'marketId':x['marketId'],**x['summary']},ensure_ascii=False),flush=True)
        for z in x['tranches']:print('  ',json.dumps({k:z[k] for k in ['key','entryPrice','qty','withinCeilingQty','aboveCeilingQty','withinEdge','aboveEdge','totalEdge','completedAt']},ensure_ascii=False))
    agg={'markets':len(rows),'tranches':sum(x['summary']['tranches'] for x in rows),'aboveCeilingQty':sum(x['summary']['aboveCeilingQty'] for x in rows),
         'withinCeilingQty':sum(x['summary']['withinCeilingQty'] for x in rows),'aboveEdge':sum(x['summary']['aboveEdge'] for x in rows),'withinEdge':sum(x['summary']['withinEdge'] for x in rows)}
    out={'version':'MS4_R2_83_TRANCHE_REPAIR_CEILING_LOSS_ATTRIBUTION_V1','researchOnly':True,'behaviorChange':False,'source':a.result,'cell':a.cell,'aggregate':agg,'rows':rows,
         'boundary':['post-hoc attribution over actual OUR fills only','ceiling=1-entryPrice is tranche accounting reference, not runtime gate','actual R257 FIFO payments and actual Repair prices only','no behavior change','winner not used for attribution']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
