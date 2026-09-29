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
REPORT=OUT/'maker_repair_curriculum_order_v0_report.json'
SEEDS=[20260820,20260821,20260822,20260823,20260824]
BATCH=256


def metrics(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),
            'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'f1':float(f1_score(y,pred,zero_division=0)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}

def train_stream(X,y,stream,seed):
    m=SGDClassifier(loss='log_loss',penalty='l2',alpha=2e-4,learning_rate='optimal',random_state=seed,average=True)
    first=True
    for i in range(0,len(stream),BATCH):
        idx=stream[i:i+BATCH]
        if first:m.partial_fit(X[idx],y[idx],classes=np.array([0,1]));first=False
        else:m.partial_fit(X[idx],y[idx])
    return m

def main():
    d,mem=build();features=CURRENT+mem
    # Independent Maker action set; do not let Taker mode priority remove Maker teacher examples.
    up=pd.to_numeric(d.label_up_next1s,errors='coerce').fillna(0).astype(int).gt(0);dn=pd.to_numeric(d.label_down_next1s,errors='coerce').fillna(0).astype(int).gt(0);maker=up|dn
    net=pd.to_numeric(d.maker_net,errors='coerce').fillna(0.0)
    repair=((net>1)&dn)|((net<-1)&up)
    md=d[maker].copy();md['yRepair']=repair[maker].astype(int).to_numpy()
    markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist();a=int(len(ids)*.70);b=int(len(ids)*.85)
    trids=set(ids[:a]);vids=set(ids[a:b]);teids=set(ids[b:])
    tr=md[md.market_id.astype(int).isin(trids)].copy();va=md[md.market_id.astype(int).isin(vids)].copy();te=md[md.market_id.astype(int).isin(teids)].copy()

    # Map training checkpoints to pure Maker lesson episodes. Only matching-label episodes count as curriculum examples.
    ep=pd.read_csv(EP);ep=ep[(ep.lessonType=='SKILL')&ep.lesson.isin(['MAKER_BUILD','MAKER_ADD','MAKER_REPAIR'])].copy()
    by={int(mid):x for mid,x in ep.groupby('marketId',sort=False)}
    pure=np.zeros(len(tr),bool);easy=np.zeros(len(tr),bool)
    for ii,r in enumerate(tr[['market_id','checkpoint_ms','yRepair']].itertuples(index=False)):
        x=by.get(int(r.market_id));
        if x is None:continue
        t=int(r.checkpoint_ms); want='MAKER_REPAIR' if int(r.yRepair)==1 else None
        z=x[(x.windowStartMs<=t)&(x.windowEndMs>=t)]
        if want is not None:z=z[z.lesson==want]
        else:z=z[z.lesson.isin(['MAKER_BUILD','MAKER_ADD'])]
        if len(z):
            pure[ii]=True
            if z.difficultyBand.isin(['EASY','MEDIUM']).any():easy[ii]=True
    if pure.sum()<100 or easy.sum()<50:raise RuntimeError(f'curriculum pools too small pure={pure.sum()} easy={easy.sum()}')

    imp=SimpleImputer(strategy='median',keep_empty_features=True);sc=StandardScaler()
    Xtr=imp.fit_transform(tr[features].apply(pd.to_numeric,errors='coerce'));Xtr=sc.fit_transform(Xtr)
    Xv=sc.transform(imp.transform(va[features].apply(pd.to_numeric,errors='coerce')));Xt=sc.transform(imp.transform(te[features].apply(pd.to_numeric,errors='coerce')))
    y=tr.yRepair.astype(int).to_numpy();yv=va.yRepair.astype(int).to_numpy();yt=te.yRepair.astype(int).to_numpy()
    n=len(tr);N=6*n;n1=int(.30*N);n2=int(.30*N);n3=N-n1-n2
    idx_all=np.arange(n);idx_pure=np.flatnonzero(pure);idx_easy=np.flatnonzero(easy)
    runs=[]
    for seed in SEEDS:
        rng=np.random.default_rng(seed)
        s1=rng.choice(idx_easy,n1,replace=True);s2=rng.choice(idx_pure,n2,replace=True);s3=rng.choice(idx_all,n3,replace=True)
        same=np.concatenate([s1,s2,s3]);shuffled=same.copy();rng.shuffle(shuffled)
        full=rng.choice(idx_all,N,replace=True)
        for policy,stream in [('CURRICULUM_EASY_PURE_MIXED',same),('SAME_SAMPLES_SHUFFLED',shuffled),('FULL_RANDOM',full)]:
            m=train_stream(Xtr,y,stream,seed);runs.append({'seed':seed,'policy':policy,'validation':metrics(yv,m.predict_proba(Xv)[:,1]),'test':metrics(yt,m.predict_proba(Xt)[:,1])})
    summary={}
    for pol in sorted({r['policy'] for r in runs}):
        rr=[r for r in runs if r['policy']==pol];summary[pol]={}
        for split in ('validation','test'):
            summary[pol][split]={k:{'mean':float(np.mean([r[split][k] for r in rr])),'std':float(np.std([r[split][k] for r in rr]))} for k in ('auc','ap','balancedAccuracy','f1','logLoss')}
    # Paired curriculum lift over exact-same-samples shuffled baseline.
    lifts=[]
    for seed in SEEDS:
        c=next(r for r in runs if r['seed']==seed and r['policy']=='CURRICULUM_EASY_PURE_MIXED');s=next(r for r in runs if r['seed']==seed and r['policy']=='SAME_SAMPLES_SHUFFLED')
        lifts.append({'seed':seed,'valAucLift':c['validation']['auc']-s['validation']['auc'],'testAucLift':c['test']['auc']-s['test']['auc'],'testApLift':c['test']['ap']-s['test']['ap'],'testLogLossDelta':c['test']['logLoss']-s['test']['logLoss']})
    rep={'reportVersion':'MAKER_REPAIR_CURRICULUM_ORDER_V0','researchOnly':True,
         'question':'With identical samples/update count/model, does easy->pure->mixed ordering improve Maker Repair learning versus shuffling the same samples?',
         'dataset':{'trainMarkets':len(trids),'validationMarkets':len(vids),'testMarkets':len(teids),'trainMakerRows':len(tr),'pureCurriculumRows':int(pure.sum()),'easyMediumPureRows':int(easy.sum()),'features':len(features),'memorySemantics':'~6/20/60s strict-past deltas'},
         'protocol':{'model':'SGDClassifier(log_loss, averaged)','seeds':SEEDS,'totalSampleUpdatesPerRun':N,'curriculumMix':'30% easy/medium pure matching skill -> 30% all pure matching skill -> 40% full mixed','sameSamplesShuffled':'exact same sampled indices globally shuffled','fullRandom':'same update count sampled from all train Maker rows'},
         'summary':summary,'pairedCurriculumVsSameShuffled':lifts,
         'pairedMean':{k:float(np.mean([z[k] for z in lifts])) for k in ('valAucLift','testAucLift','testApLift','testLogLossDelta')},
         'decisionRule':'Curriculum ordering is supported only if paired lift is directionally consistent across seeds on fixed chronological validation/test; no PnL used.',
         'guards':['No winner/PnL.','Ordinary Level-1 only.','Same examples/count for primary A/B; only order differs.','No runtime changes.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
