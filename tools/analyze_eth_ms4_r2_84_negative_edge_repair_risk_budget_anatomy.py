from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import defaultdict,deque
EPS=1e-9

def fill_states(r):
    inv={'UP':0.0,'DOWN':0.0};cost=0.0;states={}
    for x in (r.get('splitEvents') or []):
        if x.get('event')!='ROLE_FILL_SPLIT':continue
        inc=float(x.get('fillInc') or 0.0);p=float(x.get('price') or 0.0);side=str(x.get('side'))
        if inc<=EPS or side not in inv:continue
        before_floor=min(inv.values())-cost;before_best=max(inv.values())-cost
        inv[side]+=inc;cost+=inc*p
        after_floor=min(inv.values())-cost;after_best=max(inv.values())-cost
        states[(int(x.get('t') or 0),str(x.get('key')))]={'floorBefore':before_floor,'bestBefore':before_best,'floorAfter':after_floor,'bestAfter':after_best,
            'side':side,'price':p,'qty':inc,'role':x.get('role')}
    return states

def analyze(r):
    # Token-funded R278 risk fills define thesis amplification tranches.
    rf=[x for x in (r.get('r278Events') or []) if x.get('event')=='R278_INTENT_THESIS_CYCLE_REARM_FILL']
    meta={str(x['key']):x for x in rf};split=[x for x in (r.get('splitEvents') or []) if x.get('event')=='ROLE_FILL_SPLIT']
    physical=fill_states(r);tr=[];bygen=defaultdict(deque)
    for x in split:
        k=str(x.get('key'));inc=float(x.get('fillInc') or 0.0)
        if k not in meta or inc<=EPS:continue
        m=meta[k];z={'key':k,'generation':int(m.get('generation') or x.get('generationAtSubmit') or -1),'side':str(x.get('side')),
          'entryPrice':float(x.get('price') or 0.0),'qty':inc,'principal':inc*float(x.get('price') or 0.0),'remaining':inc,'payments':[]}
        tr.append(z);bygen[z['generation']].append(z)
    lookup={(int(x.get('t') or 0),str(x.get('key'))):x for x in split if float(x.get('repairAllocated') or 0)>EPS}
    for e in [x for x in (r.get('r257Events') or []) if x.get('event')=='R257_RISK_REPAIR_OBLIGATION_PAYMENT']:
        q=float(e.get('paid') or 0);gen=int(e.get('generation') or -1);key=str(e.get('key'));t=int(e.get('t') or 0)
        lk=lookup.get((t,key),{});px=float(lk.get('price') or 0.0);st=physical.get((t,key),{})
        dq=bygen[gen]
        while q>EPS and dq:
            z=dq[0]
            if z['remaining']<=EPS:dq.popleft();continue
            take=min(q,z['remaining']);q-=take;z['remaining']-=take
            ceiling=1-z['entryPrice'];edge=take*(ceiling-px);above=px>ceiling+EPS
            pay={'t':t,'key':key,'repairPrice':px,'qty':take,'ceiling':ceiling,'edge':edge,'aboveCeiling':above,
                 'active':bool(e.get('active')),'floorBeforePhysicalFill':st.get('floorBefore'),'floorAfterPhysicalFill':st.get('floorAfter'),
                 'bestBeforePhysicalFill':st.get('bestBefore'),'bestAfterPhysicalFill':st.get('bestAfter'),
                 'riskBudgetOnePrincipal':-float(z['principal']),'floorAlreadyInsideOnePrincipalBudget':(st.get('floorBefore') is not None and st['floorBefore']>=-float(z['principal'])-EPS)}
            z['payments'].append(pay)
            if z['remaining']<=EPS:dq.popleft()
    negp=[p for z in tr for p in z['payments'] if p['aboveCeiling']]
    return {'marketId':int(r['marketId']),'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'tranches':tr,
      'summary':{'tranches':len(tr),'negativeEdgePayments':len(negp),'negativeEdgeQty':sum(p['qty'] for p in negp),'negativeEdge':sum(p['edge'] for p in negp),
                 'negativeEdgeWhileFloorInsidePrincipalBudget':sum(bool(p['floorAlreadyInsideOnePrincipalBudget']) for p in negp),
                 'negativeEdgeQtyWhileFloorInsidePrincipalBudget':sum(p['qty'] for p in negp if p['floorAlreadyInsideOnePrincipalBudget'])}}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--result',required=True);ap.add_argument('--cell',default='R282_POST_REARM_FILL_FAVORABLE_REPAIR_ANCHOR');ap.add_argument('--output',required=True);a=ap.parse_args();d=json.load(open(a.result,encoding='utf-8'));rows=[]
 for r in d.get('rows',[]):
  if r.get('cell')!=a.cell:continue
  x=analyze(r);rows.append(x);print(json.dumps({'marketId':x['marketId'],**x['summary']},ensure_ascii=False),flush=True)
  for z in x['tranches']:
   for p in z['payments']:
    if p['aboveCeiling']:print(' ',z['key'],json.dumps(p,ensure_ascii=False))
 agg={'markets':len(rows),'negativeEdgePayments':sum(x['summary']['negativeEdgePayments'] for x in rows),'negativeEdgeQty':sum(x['summary']['negativeEdgeQty'] for x in rows),
      'negativeEdge':sum(x['summary']['negativeEdge'] for x in rows),'negativeEdgeWhileFloorInsidePrincipalBudget':sum(x['summary']['negativeEdgeWhileFloorInsidePrincipalBudget'] for x in rows),
      'negativeEdgeQtyWhileFloorInsidePrincipalBudget':sum(x['summary']['negativeEdgeQtyWhileFloorInsidePrincipalBudget'] for x in rows)}
 out={'version':'MS4_R2_84_NEGATIVE_EDGE_REPAIR_RISK_BUDGET_ANATOMY_V1','researchOnly':True,'behaviorChange':False,'source':a.result,'aggregate':agg,'rows':rows,
      'boundary':['post-hoc actual fill attribution only','one-principal risk budget is diagnostic because each R278 token sponsors one unit of explicit risk principal','not a runtime floor gate','no winner used for attribution']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
