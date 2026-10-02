from __future__ import annotations
import argparse,collections,json,math,os,tempfile,zipfile,sys
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED));import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import train_lane_g_r264_execution_world_v1 as wm

EPS=wm.EPS

def auc(y,p):
    y=np.asarray(y,int);return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def finalize_structural(sim):
    fills=collections.defaultdict(list);terms=collections.defaultdict(list)
    for x in sim.splitEvents:
        if x.get('event')=='ROLE_FILL_SPLIT':fills[str(x.get('key'))].append(x)
    for x in sim.slot_history:
        if x.get('event')=='SLOT_RELEASE':terms[str(x.get('key'))].append(x)
    out=[]
    for r in sim.training_rows:
        if str(r.get('route'))!='PASSIVE':continue
        t0=int(r['t']);key=str(r['key']);fs=sorted([x for x in fills.get(key,[]) if int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0));ts=sorted([x for x in terms.get(key,[]) if int(x.get('t') or 0)>=t0],key=lambda x:int(x.get('t') or 0))
        ft=int(fs[0]['t']) if fs else None;tt=int(ts[0]['t']) if ts else None
        et=min(x for x in [ft,tt] if x is not None) if ft is not None or tt is not None else None
        z=dict(r);z['firstEventObserved']=1 if et is not None else 0;z['censorLagMs']=max(0,int(sim._end_ms)-t0)
        if et is not None:
            fill_first=bool(ft is not None and (tt is None or ft<=tt));z['fillFirst']=1 if fill_first else 0;z['firstEventLagMs']=max(0,et-t0);z['firstEventLagSec']=z['firstEventLagMs']/1000.0
            z['terminalFirst']=0 if fill_first else 1
            if fill_first:
                ff=[x for x in fs if int(x.get('t') or 0)==ft];z['firstFillQty']=float(sum(float(x.get('fillInc') or 0.0) for x in ff));z['firstRepairPayQty']=float(sum(float(x.get('repairAllocated') or 0.0) for x in ff));z['firstOverflowQty']=float(sum(float(x.get('overflowRealized') or 0.0) for x in ff))
            else:z['firstFillQty']=z['firstRepairPayQty']=z['firstOverflowQty']=0.0
        else:
            z['fillFirst']=0;z['terminalFirst']=0;z['firstEventLagMs']=None;z['firstEventLagSec']=None;z['firstFillQty']=z['firstRepairPayQty']=z['firstOverflowQty']=0.0
        out.append(z)
    return out

def fit_cls(xtr,xva,ytr,yva,seed):
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(xtr,ytr);p=m.predict_proba(xva)[:,1]
    return {'n':len(yva),'positiveSupport':int(sum(yva)),'baseRate':float(np.mean(yva)),'auc':auc(yva,p),'ba':float(balanced_accuracy_score(yva,(p>=.5).astype(int))) if len(set(yva))>1 else None},m

def fit_lag(xtr,xva,ytr,yva,seed):
    yt=np.log1p(np.asarray(ytr,float));yv=np.asarray(yva,float);m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(xtr,yt);pred=np.maximum(0.,np.expm1(m.predict(xva)))
    return {'n':len(yv),'maeSec':float(mean_absolute_error(yv,pred)),'actualMeanSec':float(np.mean(yv)),'predMeanSec':float(np.mean(pred))},m

def fit_qty(xtr,xva,ytr,yva,seed):
    m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=15,l2_regularization=2.0,random_state=seed).fit(xtr,ytr);pred=np.maximum(0.,m.predict(xva));return {'n':len(yva),'mae':float(mean_absolute_error(yva,pred)),'actualMean':float(np.mean(yva)),'predMean':float(np.mean(pred))},m

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='');ap.add_argument('--train-frac',type=float,default=.70);ap.add_argument('--output',required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='lane_g_struct_world_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort=sorted(json.loads(z.read('cohort.json'))['rows'],key=lambda r:(int(r.get('windowEndMs') or 0),int(r['marketId'])))
            if a.market_ids:
                want={int(x) for x in a.market_ids.split(',') if x.strip()};cohort=[x for x in cohort if int(x['marketId']) in want]
            for cr in cohort:z.extract(f"tapes/{int(cr['marketId'])}.json.xz",root)
        rows=[];diag=[]
        for i,cr in enumerate(cohort,1):
            mid=int(cr['marketId']);sim=wm.TraceSim(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_r264('__UNSCORED__');rr=finalize_structural(sim)
            finally:sim.close()
            for q in rr:q['marketId']=mid;q['windowEndMs']=int(cr.get('windowEndMs') or 0)
            rows+=rr;obs=sum(int(x['firstEventObserved']) for x in rr);fills=sum(int(x['fillFirst']) for x in rr);diag.append({'marketId':mid,'rows':len(rr),'eventObserved':obs,'fillFirst':fills,'correct':bool(r.get('r264CorrectnessPass'))});print(json.dumps({'progress':i,'of':len(cohort),**diag[-1]},ensure_ascii=False),flush=True)
        mids=[int(x['marketId']) for x in cohort]
        if len(mids)<10:
            out={'version':'LANE_G_R264_STRUCTURAL_EVENT_WORLD_V1','researchOnly':True,'runtimeAuthority':False,'smokeOnly':True,'markets':len(mids),'rows':len(rows),'eventObserved':sum(x['firstEventObserved'] for x in rows),'fillFirst':sum(x['fillFirst'] for x in rows),'marketDiagnostics':diag,'gates':{'allBaselineCorrect':all(x['correct'] for x in diag),'rowsNonzero':len(rows)>0}}
            op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeOnly':True,'gates':out['gates'],'rows':len(rows),'eventObserved':out['eventObserved']},ensure_ascii=False));return
        cut=max(1,min(len(mids)-1,int(round(len(mids)*a.train_frac))));trm=set(mids[:cut]);tr=[r for r in rows if int(r['marketId']) in trm and r['firstEventObserved']];va=[r for r in rows if int(r['marketId']) not in trm and r['firstEventObserved']]
        xs0,xv0=wm.X(tr,False),wm.X(va,False);xs1,xv1=wm.X(tr,True),wm.X(va,True);yt=[int(r['fillFirst']) for r in tr];yv=[int(r['fillFirst']) for r in va];c0,m0=fit_cls(xs0,xv0,yt,yv,410);c1,m1=fit_cls(xs1,xv1,yt,yv,411)
        ltr=np.asarray([float(r['firstEventLagSec']) for r in tr]);lva=np.asarray([float(r['firstEventLagSec']) for r in va]);l0,lm0=fit_lag(xs0,xv0,ltr,lva,420);l1,lm1=fit_lag(xs1,xv1,ltr,lva,421)
        trf=[r for r in tr if r['fillFirst']];vaf=[r for r in va if r['fillFirst']];xf0,xvf0=wm.X(trf,False),wm.X(vaf,False);xf1,xvf1=wm.X(trf,True),wm.X(vaf,True);qtr=np.asarray([float(r['firstFillQty']) for r in trf]);qva=np.asarray([float(r['firstFillQty']) for r in vaf]);q0,qm0=fit_qty(xf0,xvf0,qtr,qva,430);q1,qm1=fit_qty(xf1,xvf1,qtr,qva,431)
        rtr=np.asarray([float(r['firstRepairPayQty']) for r in trf]);rva=np.asarray([float(r['firstRepairPayQty']) for r in vaf]);rp0,rpm0=fit_qty(xf0,xvf0,rtr,rva,440);rp1,rpm1=fit_qty(xf1,xvf1,rtr,rva,441)
        metrics={'fillFirst':{'stateOnly':c0,'stateAction':c1,'aucDeltaAction':None if c0['auc'] is None or c1['auc'] is None else c1['auc']-c0['auc']},'eventLagSec':{'stateOnly':l0,'stateAction':l1,'maeImprovementAction':l0['maeSec']-l1['maeSec']},'firstFillQty':{'stateOnly':q0,'stateAction':q1,'maeImprovementAction':q0['mae']-q1['mae']},'firstRepairPayQty':{'stateOnly':rp0,'stateAction':rp1,'maeImprovementAction':rp0['mae']-rp1['mae']}}
        gates={'allBaselineCorrect':all(x['correct'] for x in diag),'observedEventCoverageAbove95':sum(x['firstEventObserved'] for x in rows)/max(1,len(rows))>=.95,'fillFirstActionIncrementPositive':(metrics['fillFirst']['aucDeltaAction'] or -1)>0,'eventLagActionIncrementPositive':metrics['eventLagSec']['maeImprovementAction']>0,'firstFillQtyActionIncrementNonnegative':metrics['firstFillQty']['maeImprovementAction']>=0}
        out={'version':'LANE_G_R264_STRUCTURAL_EVENT_WORLD_V1','researchOnly':True,'runtimeAuthority':False,'markets':len(mids),'trainMarkets':len(trm),'validationMarkets':len(mids)-len(trm),'rows':len(rows),'observedRows':len(tr)+len(va),'censoredRows':len(rows)-len(tr)-len(va),'stateFeatures':wm.STATE,'actionFeatures':wm.ACTION+['roleOneHot','routeOneHot','sideUp'],'metrics':metrics,'marketDiagnostics':diag,'gates':gates,'structuralPass':all(gates.values()),'boundary':['exact R2.64 realistic-HFT PASSIVE carrier submissions','label is next physical FILL vs SLOT_RELEASE terminal event; no fixed decision window','same-receipt fill+terminal counts as FILL_FIRST because physical fill occurred','event lag is submit->first structural event','chronological market split','winner/Target future absent','no fresh/no dream fill/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');joblib.dump({'version':out['version'],'stateFeatures':wm.STATE,'actionFeatures':wm.ACTION,'models':{'fillFirst':m1,'eventLagLog':lm1,'firstFillQty':qm1,'firstRepairPayQty':rpm1}},op.parent/'structural_event_models.joblib');
        with (op.parent/'structural_rows.jsonl').open('w',encoding='utf-8') as f:
            for x in rows:f.write(json.dumps(x,ensure_ascii=False)+'\n')
        print(json.dumps({'ok':True,'structuralPass':out['structuralPass'],'gates':gates,'metrics':metrics,'rows':len(rows),'censored':out['censoredRows']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
