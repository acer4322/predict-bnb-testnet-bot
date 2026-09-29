from __future__ import annotations
import json
from pathlib import Path
import numpy as np
# reuse builder/simulation helpers from v0
import sys
sys.path.insert(0,str(Path('tools').resolve()))
import test_r3_formation_hysteresis_controller_v0 as v0

R=Path('data/research/r3_v0')

def run_cfg(data,cross_on,build_on,build_off,min_dwell):
    by={}
    for row in data: by.setdefault(row[0],[]).append(row)
    classes=['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']
    idx={c:i for i,c in enumerate(classes)}
    cm=np.zeros((3,3),dtype=int); trans=0; dwell=[]; total=0
    for mid,rows in by.items():
        X=np.asarray([r[2] for r in rows],float); pa,pc=v0.probs(X); state='ALLOW_ASYMMETRY'; since=rows[0][1]; prev=since
        for k,r in enumerate(rows):
            t=r[1]; teacher=r[3]; can=(t-since)>=min_dwell; new=state
            if pc[k]>=cross_on and can:new='CROSSING_PROTECTION'
            elif state=='CROSSING_PROTECTION':
                if pc[k] < cross_on*.65 and can:new='BUILD_WEAK_SIDE' if pa[k]>=build_on else 'ALLOW_ASYMMETRY'
            elif state=='ALLOW_ASYMMETRY':
                if pa[k]>=build_on and can:new='BUILD_WEAK_SIDE'
            elif state=='BUILD_WEAK_SIDE':
                if pa[k]<=build_off and can:new='ALLOW_ASYMMETRY'
            if new!=state:dwell.append(max(0,t-since));trans+=1;state=new;since=t
            cm[idx[teacher],idx[state]]+=1; total+=1; prev=t
        dwell.append(max(0,prev-since))
    recalls=[]; precs=[]
    for i in range(3):
        recalls.append(cm[i,i]/cm[i].sum() if cm[i].sum() else 0)
        precs.append(cm[i,i]/cm[:,i].sum() if cm[:,i].sum() else 0)
    pred=cm.sum(axis=0)/max(1,total); teach=cm.sum(axis=1)/max(1,total)
    bal=float(np.mean(recalls)); dist=float(np.abs(pred-teach).sum())
    return {'crossOn':cross_on,'buildOn':build_on,'buildOff':build_off,'minDwellMs':min_dwell,'accuracy':float(np.trace(cm)/max(1,total)),'balancedRecall':bal,'stateL1Distance':dist,'transitionsPerMarket':trans/max(1,len(by)),'medianDwellMs':float(np.median(dwell)) if dwell else None,'recall':dict(zip(classes,recalls)),'precision':dict(zip(classes,precs)),'predRates':dict(zip(classes,pred.tolist())),'teacherRates':dict(zip(classes,teach.tolist())),'confusion':cm.tolist()}

def main():
    import sqlite3
    c=sqlite3.connect(v0.DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
    for mid in mids:data.extend(v0.build_market(c,mid))
    c.close(); uniq=sorted(set(r[0] for r in data)); test=set(uniq[int(.8*len(uniq)):]); data=[r for r in data if r[0] in test]
    out=[]
    for cross in [.25,.3,.35,.4,.45,.5,.55]:
      for bon,boff in [(.42,.32),(.45,.35),(.48,.38),(.5,.4),(.52,.42),(.55,.45)]:
       for dwell in [0,1000,2000,3000,5000]:out.append(run_cfg(data,cross,bon,boff,dwell))
    # prioritize balanced class recall + reasonable state-distribution match, then accuracy
    ranked=sorted(out,key=lambda z:-(z['balancedRecall']-.35*z['stateL1Distance']+.15*z['accuracy']))
    rep={'version':'R3_FORMATION_HYSTERESIS_CALIBRATION_V1','testMarkets':len(test),'testRows':len(data),'best':ranked[:20]}
    (R/'r3_formation_hysteresis_calibration_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep['best'][:10],indent=2))
if __name__=='__main__':main()
