from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile,sys,joblib
from pathlib import Path
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import train_lane_g_r264_execution_world_v1 as wm
from tools import train_lane_g_r264_structural_event_world_v1 as sw
EPS=1e-9;GRID=0.01
QUEUE=['queueVisible','queueVisibleToOrder','distanceFromSameBestTicks','sameTopQty','oppositeTopQty','sameTop3Qty','oppositeTop3Qty','nativeSpreadTicks','nativeBookImbalance','sameBookLevels','oppositeBookLevels','ownLevelIsBest']

def finite(x,d=np.nan):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except:return d

def queue_feats(book,side,price,qty):
    bids=book.get('bids') or {};asks=book.get('asks') or {};bb=max(bids) if bids else math.nan;ba=min(asks) if asks else math.nan
    if side=='UP':
        same=bids;opp=asks;native=float(price);sb=bb;dist=(sb-native)/GRID if math.isfinite(sb) else math.nan;same_prices=sorted(bids,reverse=True)[:3];opp_prices=sorted(asks)[:3]
    else:
        same=asks;opp=bids;native=round(1.0-float(price),10);sb=ba;dist=(native-sb)/GRID if math.isfinite(sb) else math.nan;same_prices=sorted(asks)[:3];opp_prices=sorted(bids,reverse=True)[:3]
    own=float(same.get(round(native,10),0.0));same_top=float(same.get(round(sb,10),0.0)) if math.isfinite(sb) else 0.0
    ob=ba if side=='UP' else bb;opp_top=float(opp.get(round(ob,10),0.0)) if math.isfinite(ob) else 0.0
    b3=sum(float(bids[p]) for p in sorted(bids,reverse=True)[:3]);a3=sum(float(asks[p]) for p in sorted(asks)[:3]);den=b3+a3
    return {'queueVisible':own,'queueVisibleToOrder':own/max(float(qty),EPS),'distanceFromSameBestTicks':finite(dist),'sameTopQty':same_top,'oppositeTopQty':opp_top,'sameTop3Qty':sum(float(same[p]) for p in same_prices),'oppositeTop3Qty':sum(float(opp[p]) for p in opp_prices),'nativeSpreadTicks':(ba-bb)/GRID if math.isfinite(bb) and math.isfinite(ba) else np.nan,'nativeBookImbalance':(b3-a3)/den if den>EPS else 0.0,'sameBookLevels':float(len(same)),'oppositeBookLevels':float(len(opp)),'ownLevelIsBest':float(math.isfinite(sb) and abs(float(sb)-native)<=1e-9)}

class DecisionTraceSim(wm.TraceSim):
    def _state_row(self,t,side,role,price,qty,route,key,source):
        r=super()._state_row(t,side,role,price,qty,route,key,source);r.update(queue_feats(self.book,str(side),float(price),float(qty)));return r

def mat(rows,with_queue):
    base=wm.X(rows,True)
    if not with_queue:return base
    q=np.asarray([[finite(r.get(f)) for f in QUEUE] for r in rows],float);return np.c_[base,q]
def model(kind):
    if kind=='LOGISTIC':return Pipeline([('imp',SimpleImputer(strategy='median',keep_empty_features=True)),('sc',StandardScaler()),('m',LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=20260907))])
    return Pipeline([('imp',SimpleImputer(strategy='median',keep_empty_features=True)),('m',ExtraTreesClassifier(n_estimators=300,min_samples_leaf=15,max_features='sqrt',class_weight='balanced',random_state=20260907,n_jobs=1))])
def metrics(y,p,mids,prior,other=None):
    y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-6,1-1e-6);pp=np.full(len(y),float(prior));mids=np.asarray(mids,int)
    o={'n':len(y),'positive':int(y.sum()),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'logloss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'priorLogloss':float(log_loss(y,pp,labels=[0,1])),'priorBrier':float(brier_score_loss(y,pp)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,(p>=.5).astype(int))) if len(np.unique(y))>1 else None}
    wins=0;details=[]
    for m in sorted(set(mids)):
        ix=np.where(mids==m)[0];ll=float(log_loss(y[ix],p[ix],labels=[0,1]));d={'marketId':int(m),'n':len(ix),'logloss':ll}
        if other is not None:
            op=np.clip(np.asarray(other,float)[ix],1e-6,1-1e-6);bl=float(log_loss(y[ix],op,labels=[0,1]));d['baselineLogloss']=bl;d['winVsBaseline']=bool(ll<bl-1e-12);wins+=int(d['winVsBaseline'])
        details.append(d)
    if other is not None:o['marketWinsVsBaseline']=wins
    o['marketDetails']=details;return o

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='lane_g_decisionq_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort=sorted(json.loads(z.read('cohort.json'))['rows'],key=lambda r:(int(r.get('windowEndMs') or 0),int(r['marketId'])))
            for cr in cohort:z.extract(f"tapes/{int(cr['marketId'])}.json.xz",root)
        rows=[];diag=[]
        for i,cr in enumerate(cohort,1):
            m=int(cr['marketId']);sim=DecisionTraceSim(root/'tapes'/f'{m}.json.xz')
            try:r=sim.run_r264('__UNSCORED__');rr=sw.finalize_structural(sim)
            finally:sim.close()
            for x in rr:x['marketId']=m;x['windowEndMs']=int(cr.get('windowEndMs') or 0)
            rows+=rr;diag.append({'marketId':m,'rows':len(rr),'observed':sum(int(x['firstEventObserved']) for x in rr),'correct':bool(r.get('r264CorrectnessPass'))})
            if i%20==0:print(json.dumps({'progress':i,'of':len(cohort),'rows':len(rows)}),flush=True)
        mids=[int(x['marketId']) for x in cohort];cut=70;trm=set(mids[:cut]);tem=set(mids[cut:]);tr=[r for r in rows if int(r['marketId']) in trm and int(r['firstEventObserved'])==1 and str(r.get('route'))=='PASSIVE'];te=[r for r in rows if int(r['marketId']) in tem and int(r['firstEventObserved'])==1 and str(r.get('route'))=='PASSIVE']
        ytr=np.asarray([int(r['fillFirst']) for r in tr]);yte=np.asarray([int(r['fillFirst']) for r in te]);mt=np.asarray([int(r['marketId']) for r in te]);prior=float(ytr.mean());pred={};fitted={};outm={}
        for kind in ('LOGISTIC','EXTRA_TREES'):
            b=model(kind);q=model(kind);b.fit(mat(tr,False),ytr);q.fit(mat(tr,True),ytr);pb=b.predict_proba(mat(te,False))[:,1];pq=q.predict_proba(mat(te,True))[:,1];pred[kind]=(pb,pq);fitted[kind]={'BASE':b,'BASE_QUEUE':q};bm=metrics(yte,pb,mt,prior);qm=metrics(yte,pq,mt,prior,pb);outm[kind]={'BASE':bm,'BASE_QUEUE':qm,'delta':{'auc':(qm['auc']-bm['auc']) if qm['auc'] is not None and bm['auc'] is not None else None,'logloss':bm['logloss']-qm['logloss'],'brier':bm['brier']-qm['brier'],'marketWins':qm.get('marketWinsVsBaseline',0)}}
        gates={}
        for k,z in outm.items():
            d=z['delta'];g=bool((d['auc'] or -9)>0 and d['logloss']>0 and d['brier']>0 and d['marketWins']>=21);gates[k]={'overallImproves':bool((d['auc'] or -9)>0 and d['logloss']>0 and d['brier']>0),'market21of30':bool(d['marketWins']>=21),'pass':g,'calibrationBeatsPrior':bool(z['BASE_QUEUE']['logloss']<z['BASE_QUEUE']['priorLogloss'] and z['BASE_QUEUE']['brier']<z['BASE_QUEUE']['priorBrier'])}
        out={'version':'LANE_G_DECISION_QUEUE_STRUCTURAL_EVENT_MODEL_V2_20260907','researchOnly':True,'runtimeAuthority':False,'coverage':{'rows':len(rows),'passiveObservedTrain':len(tr),'passiveObservedValidation':len(te),'markets':len(mids),'trainMarkets':70,'validationMarkets':30,'trainFillRate':prior,'validationFillRate':float(yte.mean())},'split':{'trainMarketIds':mids[:70],'validationMarketIds':mids[70:]},'queueFeatures':QUEUE,'metrics':outm,'gates':gates,'decisionQueuePass':any(v['pass'] for v in gates.values()),'allBaselineCorrect':all(x['correct'] for x in diag),'boundary':['queue/book sampled inside exact R2.64 simulator at action decision receipt t','no exchange-acceptance or post-decision data','same chronological 70/30 H100 split','first structural event label only','no fixed decision window','no winner/terminal PnL/Target future','no threshold sweep','consumed only/no fresh/no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');mp=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'decision_queue_models.joblib') if a.model_output.upper()=='AUTO' else Path(a.model_output);joblib.dump({'version':out['version'],'queueFeatures':QUEUE,'models':fitted,'split':out['split'],'trainPrior':prior},mp)
        compact={k:{'BASE':{kk:v for kk,v in z['BASE'].items() if kk!='marketDetails'},'BASE_QUEUE':{kk:v for kk,v in z['BASE_QUEUE'].items() if kk!='marketDetails'},'delta':z['delta']} for k,z in outm.items()}
        print(json.dumps({'ok':True,'coverage':out['coverage'],'metrics':compact,'gates':gates,'decisionQueuePass':out['decisionQueuePass'],'allBaselineCorrect':out['allBaselineCorrect']},indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
