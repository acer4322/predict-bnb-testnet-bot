from __future__ import annotations
import argparse,json,math
from pathlib import Path
from collections import deque

EPS=1e-9
TOL=1e-8

def apply_fill(un, side, qty, price):
    opp='DOWN' if side=='UP' else 'UP'
    left=float(qty)
    while left>EPS and un[opp]:
        oq,op=un[opp][0]
        m=min(left,oq)
        left-=m; oq-=m
        if oq<=EPS: un[opp].popleft()
        else: un[opp][0]=(oq,op)
    if left>EPS: un[side].append((left,float(price)))

def qsum(dq): return float(sum(q for q,_ in dq))
def qavg(dq):
    q=qsum(dq)
    return float(sum(a*p for a,p in dq)/q) if q>EPS else None

def audit(path, name):
    d=json.load(open(path,encoding='utf-8'))
    out=[]; integrity=[]
    total_model=obs=viol=latent=0
    total_qty=obs_qty=viol_qty=0.0
    total_notional=obs_notional=viol_notional=0.0
    viol_mids=[]
    for m in d['markets']:
        mid=int(m['marketId'])
        fills=sorted(m.get('fillEvents',[]),key=lambda z:(int(z['t']),int(z.get('fillOrdinal',0))))
        acts=sorted(m.get('actionAttempts',[]),key=lambda z:(int(z['t']),int(z.get('attemptOrdinal',0))))
        un={'UP':deque(),'DOWN':deque()}; fi=0; mv=mo=ml=0
        rows=[]
        for a in acts:
            t=int(a['t'])
            while fi<len(fills) and int(fills[fi]['t'])<=t:
                f=fills[fi]; apply_fill(un,str(f['side']),float(f['qty']),float(f['price'])); fi+=1
            # Verify reconstructed unmatched against trace at each model-driven attempt before classifying.
            if a.get('strictPastFeature') is None or not bool(a.get('accepted')):
                continue
            rec_up=qsum(un['UP']); rec_dn=qsum(un['DOWN'])
            tr=a.get('unmatched') or {}
            tu=float(tr.get('UP',0.0)); td=float(tr.get('DOWN',0.0))
            du=abs(rec_up-tu); dd=abs(rec_dn-td)
            if du>TOL or dd>TOL:
                integrity.append({'marketId':mid,'attemptOrdinal':a.get('attemptOrdinal'),'t':t,'reconstructed':{'UP':rec_up,'DOWN':rec_dn},'trace':{'UP':tu,'DOWN':td},'delta':{'UP':du,'DOWN':dd}})
                continue
            side=str(a['side']); opp='DOWN' if side=='UP' else 'UP'; price=float(a['price']); qty=float(a['qty']); notional=price*qty
            total_model+=1; mv+=1; total_qty+=qty; total_notional+=notional
            oppq=qsum(un[opp]); sameq=qsum(un[side])
            if oppq>EPS:
                avg=qavg(un[opp]); pair_sum=float(avg+price); ok=pair_sum<=1.0000001
                obs+=1; mo+=1; obs_qty+=qty; obs_notional+=notional
                if not ok:
                    viol+=1; mvio=1; viol_qty+=qty; viol_notional+=notional
                else: mvio=0
                rows.append({'attemptOrdinal':int(a['attemptOrdinal']),'t':t,'side':side,'price':price,'qty':qty,'oppUnmatchedQty':oppq,'sameUnmatchedQty':sameq,'oppUnmatchedAvg':avg,'pairSum':pair_sum,'teacherObservableEconOk':ok,'violation':bool(mvio)})
            else:
                latent+=1; ml+=1
                rows.append({'attemptOrdinal':int(a['attemptOrdinal']),'t':t,'side':side,'price':price,'qty':qty,'oppUnmatchedQty':0.0,'sameUnmatchedQty':sameq,'branch':'LATENT_HEADROOM','teacherObservableEconOk':None})
        if any(r.get('violation') for r in rows): viol_mids.append(mid)
        out.append({'marketId':mid,'modelAcceptedActions':mv,'observablePairActions':mo,'observablePairViolations':sum(1 for r in rows if r.get('violation')),'latentHeadroomActions':ml,'actionAudit':rows})
    if integrity:
        return {'cohort':name,'status':'STOP_DATA_INTEGRITY','integrityViolations':integrity[:100],'integrityViolationCount':len(integrity)}
    return {'cohort':name,'status':'PASS_INTEGRITY','summary':{
        'markets':len(out),'modelAcceptedActions':total_model,'observablePairActions':obs,'observablePairViolations':viol,
        'observableViolationRate':viol/obs if obs else None,
        'violationQtyShareOfObservable':viol_qty/obs_qty if obs_qty else None,
        'violationNotionalShareOfObservable':viol_notional/obs_notional if obs_notional else None,
        'latentHeadroomActions':latent,'latentHeadroomShare':latent/total_model if total_model else None,
        'marketsWithObservableViolations':len(set(viol_mids)),'marketIdsWithObservableViolations':sorted(set(viol_mids))
    },'markets':out}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--test20',required=True);ap.add_argument('--fresh101',required=True);ap.add_argument('--external24',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args()
    reps=[audit(a.test20,'TEST20'),audit(a.fresh101,'FRESH101'),audit(a.external24,'EXTERNAL24')]
    if any(r['status']!='PASS_INTEGRITY' for r in reps): verdict='STOP_DATA_INTEGRITY'
    else:
        total=sum(r['summary']['observablePairViolations'] for r in reps)
        verdict='NOT_EXERCISED_STOP_THIS_PREMISE' if total==0 else 'STRUCTURAL_CONTRADICTION_EXERCISED'
    out={'version':'DAGGER60_FACTORIZED_HEAD_JOINT_ADMISSIBILITY_GAP_V1','verdict':verdict,'cohorts':reps}
    p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'summaries':{r['cohort']:r.get('summary') for r in reps}},indent=2))
if __name__=='__main__':main()
