from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, f1_score, log_loss
from sklearn.preprocessing import StandardScaler

from train_supervisor_mode_v0 import build, CURRENT

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_curriculum_v0'
EP=OUT/'ordinary_curriculum_episodes_v1.csv'
REPORT=OUT/'maker_repair_progress_teacher_v0_report.json'
SEEDS=[20260820,20260821,20260822,20260823,20260824]
BATCH=256
EMA=.75
EPSILON=.15


def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),
            'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'f1':float(f1_score(y,pred,zero_division=0)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}

def new_model(seed):
    return SGDClassifier(loss='log_loss',penalty='l2',alpha=2e-4,learning_rate='optimal',random_state=seed,average=True)

def main():
    d,mem=build();features=CURRENT+mem
    up=pd.to_numeric(d.label_up_next1s,errors='coerce').fillna(0).astype(int).gt(0);dn=pd.to_numeric(d.label_down_next1s,errors='coerce').fillna(0).astype(int).gt(0);maker=up|dn
    net=pd.to_numeric(d.maker_net,errors='coerce').fillna(0.0);repair=((net>1)&dn)|((net<-1)&up)
    md=d[maker].copy();md['yRepair']=repair[maker].astype(int).to_numpy()
    markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist();a=int(len(ids)*.70);b=int(len(ids)*.85)
    train_ids=ids[:a];val_ids=set(ids[a:b]);test_ids=set(ids[b:]);probe_n=max(20,int(len(train_ids)*.10));update_ids=set(train_ids[:-probe_n]);probe_ids=set(train_ids[-probe_n:])
    upd=md[md.market_id.astype(int).isin(update_ids)].copy().reset_index(drop=True);probe=md[md.market_id.astype(int).isin(probe_ids)].copy().reset_index(drop=True);va=md[md.market_id.astype(int).isin(val_ids)].copy();te=md[md.market_id.astype(int).isin(test_ids)].copy()

    ep=pd.read_csv(EP);ep=ep[(ep.lessonType=='SKILL')&ep.lesson.isin(['MAKER_BUILD','MAKER_ADD','MAKER_REPAIR'])].copy();by={int(mid):x for mid,x in ep.groupby('marketId',sort=False)}
    order={'EASY':0,'MEDIUM':1,'HARD':2,'VERY_HARD':2}
    def assign(frame):
        out=[]
        for r in frame[['market_id','checkpoint_ms','yRepair']].itertuples(index=False):
            x=by.get(int(r.market_id));bucket='MIXED'
            if x is not None:
                t=int(r.checkpoint_ms);z=x[(x.windowStartMs<=t)&(x.windowEndMs>=t)]
                if int(r.yRepair):z=z[z.lesson=='MAKER_REPAIR']
                else:z=z[z.lesson.isin(['MAKER_BUILD','MAKER_ADD'])]
                if len(z):
                    bands=sorted([order.get(str(v),2) for v in z.difficultyBand]);k=bands[0]
                    bucket=['PURE_EASY','PURE_MEDIUM','PURE_HARD'][k]
            out.append(bucket)
        return np.asarray(out,object)
    upd['bucket']=assign(upd);probe['bucket']=assign(probe)
    buckets=['PURE_EASY','PURE_MEDIUM','PURE_HARD','MIXED']
    bucket_idx={k:np.flatnonzero(upd.bucket.astype(str).to_numpy()==k) for k in buckets}
    probe_idx={k:np.flatnonzero(probe.bucket.astype(str).to_numpy()==k) for k in buckets}
    buckets=[k for k in buckets if len(bucket_idx[k])>=100 and len(probe_idx[k])>=20]

    imp=SimpleImputer(strategy='median',keep_empty_features=True);sc=StandardScaler()
    Xu=sc.fit_transform(imp.fit_transform(upd[features].apply(pd.to_numeric,errors='coerce')));Xp=sc.transform(imp.transform(probe[features].apply(pd.to_numeric,errors='coerce')));Xv=sc.transform(imp.transform(va[features].apply(pd.to_numeric,errors='coerce')));Xt=sc.transform(imp.transform(te[features].apply(pd.to_numeric,errors='coerce')))
    yu=upd.yRepair.astype(int).to_numpy();yp=probe.yRepair.astype(int).to_numpy();yv=va.yRepair.astype(int).to_numpy();yt=te.yRepair.astype(int).to_numpy()
    steps=max(100,int(np.ceil(6*len(upd)/BATCH)))

    def run(seed,policy):
        rng=np.random.default_rng(seed);m=new_model(seed);first=True;scores={k:0.0 for k in buckets};counts={k:0 for k in buckets};pick=[];reward_hist=[]
        def fit_idx(idx):
            nonlocal first
            if first:m.partial_fit(Xu[idx],yu[idx],classes=np.array([0,1]));first=False
            else:m.partial_fit(Xu[idx],yu[idx])
        # Initialize every teacher with one pass over each bucket so all classes/model state are live.
        for k in buckets:
            idx=rng.choice(bucket_idx[k],BATCH,replace=len(bucket_idx[k])<BATCH);fit_idx(idx);counts[k]+=1;pick.append(k)
        for step in range(len(buckets),steps):
            if policy=='PROGRESS_TEACHER':
                if rng.random()<EPSILON:k=str(rng.choice(buckets))
                else:
                    bonus={z:0.002/np.sqrt(counts[z]+1) for z in buckets};k=max(buckets,key=lambda z:scores[z]+bonus[z])
            elif policy=='UNIFORM_BUCKET':k=str(rng.choice(buckets))
            else:k='FULL'
            if k=='FULL':
                idx=rng.choice(np.arange(len(upd)),BATCH,replace=False if len(upd)>=BATCH else True);fit_idx(idx);pick.append(k);continue
            pi=probe_idx[k];before=log_loss(yp[pi],np.clip(m.predict_proba(Xp[pi])[:,1],1e-7,1-1e-7),labels=[0,1])
            idx=rng.choice(bucket_idx[k],BATCH,replace=len(bucket_idx[k])<BATCH);fit_idx(idx)
            after=log_loss(yp[pi],np.clip(m.predict_proba(Xp[pi])[:,1],1e-7,1-1e-7),labels=[0,1]);reward=max(0.0,before-after)
            scores[k]=EMA*scores[k]+(1-EMA)*reward;counts[k]+=1;pick.append(k);reward_hist.append((k,reward))
        return {'validation':metric(yv,m.predict_proba(Xv)[:,1]),'test':metric(yt,m.predict_proba(Xt)[:,1]),'picks':dict(pd.Series(pick).value_counts()),'finalScores':scores,'meanReward':{k:float(np.mean([r for z,r in reward_hist if z==k])) if any(z==k for z,_ in reward_hist) else 0.0 for k in buckets}}

    runs=[]
    for seed in SEEDS:
        for pol in ('PROGRESS_TEACHER','UNIFORM_BUCKET','FULL_RANDOM'):
            z=run(seed,pol);runs.append({'seed':seed,'policy':pol,**z})
    summary={}
    for pol in ('PROGRESS_TEACHER','UNIFORM_BUCKET','FULL_RANDOM'):
        rr=[r for r in runs if r['policy']==pol];summary[pol]={}
        for split in ('validation','test'):
            summary[pol][split]={k:{'mean':float(np.mean([r[split][k] for r in rr])),'std':float(np.std([r[split][k] for r in rr]))} for k in ('auc','ap','balancedAccuracy','f1','logLoss')}
        if pol!='FULL_RANDOM':summary[pol]['meanPicks']={k:float(np.mean([r['picks'].get(k,0) for r in rr])) for k in buckets}
    paired=[]
    for seed in SEEDS:
        t=next(r for r in runs if r['seed']==seed and r['policy']=='PROGRESS_TEACHER');u=next(r for r in runs if r['seed']==seed and r['policy']=='UNIFORM_BUCKET');f=next(r for r in runs if r['seed']==seed and r['policy']=='FULL_RANDOM')
        paired.append({'seed':seed,'teacherVsUniformTestAuc':t['test']['auc']-u['test']['auc'],'teacherVsFullTestAuc':t['test']['auc']-f['test']['auc'],'teacherVsUniformValAuc':t['validation']['auc']-u['validation']['auc']})
    rep={'reportVersion':'MAKER_REPAIR_PROGRESS_TEACHER_V0','researchOnly':True,
         'question':'Can a learning-progress curriculum teacher select lesson buckets better than uniform bucket sampling or full-random training for Maker Repair?',
         'dataset':{'updateMarkets':len(update_ids),'probeMarkets':len(probe_ids),'validationMarkets':len(val_ids),'testMarkets':len(test_ids),'updateRows':len(upd),'probeRows':len(probe),'buckets':buckets,'bucketUpdateCounts':{k:int(len(bucket_idx[k])) for k in buckets},'bucketProbeCounts':{k:int(len(probe_idx[k])) for k in buckets},'features':len(features)},
         'teacher':{'reward':'positive reduction in same-bucket internal-probe log-loss after a batch','scoreEma':EMA,'epsilonExplore':EPSILON,'steps':steps,'batch':BATCH,'visibility':'final validation/test never visible to teacher'},
         'summary':summary,'paired':paired,'pairedMean':{k:float(np.mean([r[k] for r in paired])) for k in ('teacherVsUniformTestAuc','teacherVsFullTestAuc','teacherVsUniformValAuc')},
         'decisionRule':'V0 supports automatic curriculum only if progress teacher shows reasonably consistent chronological holdout lift; otherwise retain curriculum catalog but improve teacher reward/buckets before scaling.',
         'guards':['No winner/PnL.','Probe is inside training chronology and excluded from student updates.','Validation/test never visible to teacher.','No runtime changes.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
