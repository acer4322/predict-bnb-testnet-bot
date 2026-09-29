from __future__ import annotations
import json, joblib
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
HFT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_lifecycle_dataset_fresh21_v1.json'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_teacher_economic_progress_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_teacher_economic_progress_v1.json'
ROWS=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_teacher_economic_progress_rows_v1.csv'
EPS=1e-9
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
FULL=BASE+PROG

def model(seed:int):
    return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)

def metrics(y,p):
    y=np.asarray(y,dtype=int); p=np.asarray(p,dtype=float)
    o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'meanProbability':float(p.mean()) if len(p) else None}
    if len(y) and len(np.unique(y))>1:
        o.update({'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1])),'brier':float(brier_score_loss(y,p)),'calibrationGap':float(p.mean()-y.mean())})
    else:
        o.update({'auc':None,'ap':None,'logLoss':None,'brier':None,'calibrationGap':None})
    return o

def add_future_labels(d:pd.DataFrame)->pd.DataFrame:
    out=[]
    for mid,g in d.groupby('market_id',sort=False):
        g=g.sort_values('t').reset_index(drop=True)
        ts=g.t.to_numpy(np.int64)
        risks=g.risk_deficit.to_numpy(float); gaps=g.abs_gap.to_numpy(float)
        for i,r in enumerate(g.itertuples(index=False)):
            if not (60.0 <= float(r.seconds_left) <= 300.0): continue
            if float(r.weak_active_owners) <= 0: continue
            target=int(r.t)+5000
            j=int(np.searchsorted(ts,target,side='left'))
            if j>=len(g) or int(ts[j])>int(r.t)+10000: continue
            z={c:getattr(r,c) for c in g.columns}
            z['future_t']=int(ts[j]); z['future_horizon_ms']=int(ts[j]-int(r.t))
            z['future_risk_deficit']=float(risks[j]); z['future_abs_gap']=float(gaps[j])
            z['floor_repair_progress']=int(float(r.risk_deficit)>EPS and risks[j] < float(r.risk_deficit)-EPS)
            z['pair_balance_progress']=int(float(r.abs_gap)>EPS and gaps[j] < float(r.abs_gap)-EPS)
            out.append(z)
    return pd.DataFrame(out)

def chronological_blocks(e:pd.DataFrame):
    ms=e.groupby('market_id').t.min().sort_values().index.astype(int).tolist()
    initial=min(200,max(120,int(len(ms)*2/3))); rem=len(ms)-initial
    sizes=[rem//4]*4
    for i in range(rem%4): sizes[i]+=1
    cur=initial; blocks=[]
    for bi,sz in enumerate(sizes,1):
        trm=ms[:cur]; tem=ms[cur:cur+sz]; cur+=sz
        blocks.append((bi,trm,tem))
    return ms,blocks

def eval_head(tr,te,label,feature_set,seed):
    tr=tr.copy(); te=te.copy()
    if label=='floor_repair_progress':
        tr=tr[tr.risk_deficit>EPS]; te=te[te.risk_deficit>EPS]
    else:
        tr=tr[tr.abs_gap>EPS]; te=te[te.abs_gap>EPS]
    if tr[label].nunique()<2 or te[label].nunique()<2: return None,None
    m=model(seed).fit(tr[feature_set],tr[label].astype(int))
    p=m.predict_proba(te[feature_set])[:,list(m.classes_).index(1)]
    return metrics(te[label],p),m

def summarize(blocks,key):
    q=[b[key] for b in blocks if b.get(key) and b[key].get('auc') is not None]
    return {'blocks':len(q),'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBrier':float(np.mean([x['brier'] for x in q])),'meanAbsCalibrationGap':float(np.mean([abs(x['calibrationGap']) for x in q])),'blockAucs':[float(x['auc']) for x in q]}

def first_checkpoint_rows(path:Path):
    d=json.loads(path.read_text(encoding='utf-8'))
    rows=d.get('rows') or []
    first={}
    for r in rows:
        rid=str(r.get('checkpointResponsibilityId') or '')
        if not rid: continue
        k=(int(r['marketId']),rid)
        if k not in first or int(r['t'])<int(first[k]['t']): first[k]=r
    return list(first.values()),d

def hft_transfer(final_models):
    rows,raw=first_checkpoint_rows(HFT)
    frozen=joblib.load(STACK); fm=frozen['M0_model']; ff=list(frozen['features']['full'])
    out={'cohort':{'markets':raw.get('cohort',{}).get('markets'),'firstCheckpointRoots':len(rows),'executionCoreExact':raw.get('integrity',{}).get('executionCoreExact')}}
    mapping=[('FLOOR_REPAIR_PROGRESS','floorImproved5s','FLOOR_FULL'),('PAIR_BALANCE_PROGRESS','absNetReduced5s','PAIR_FULL')]
    for name,label,mkey in mapping:
        use=[r for r in rows if all(k in r and r[k] is not None for k in FULL+[label])]
        if name=='FLOOR_REPAIR_PROGRESS': use=[r for r in use if float(r['risk_deficit'])>EPS]
        else: use=[r for r in use if float(r['abs_gap'])>EPS]
        y=np.asarray([int(r[label]) for r in use],int)
        X=np.asarray([[float(r[f]) for f in FULL] for r in use],float)
        pnew=final_models[mkey].predict_proba(X)[:,list(final_models[mkey].classes_).index(1)]
        Xf=np.asarray([[float(r[f]) for f in ff] for r in use],float)
        pold=fm.predict_proba(Xf)[:,list(fm.classes_).index(1)]
        out[name]={'label':label,'DIRECT_ECONOMIC_TEACHER':metrics(y,pnew),'FROZEN_M0_ACTION_TEACHER':metrics(y,pold),'deltaAuc':float(roc_auc_score(y,pnew)-roc_auc_score(y,pold)) if len(np.unique(y))>1 else None,'deltaLogLossImprovement':float(log_loss(y,np.clip(pold,1e-6,1-1e-6),labels=[0,1])-log_loss(y,np.clip(pnew,1e-6,1-1e-6),labels=[0,1])) if len(np.unique(y))>1 else None}
    return out

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'))
    d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL+['market_id','t','seconds_left','abs_gap','risk_deficit']).copy()
    e=add_future_labels(d).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL+['future_risk_deficit','future_abs_gap']).copy()
    e.to_csv(ROWS,index=False)
    ms,split=chronological_blocks(e); blocks=[]
    for bi,trm,tem in split:
        tr=e[e.market_id.isin(trm)]; te=e[e.market_id.isin(tem)]
        b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testRows':int(len(te))}
        for label,prefix in [('floor_repair_progress','FLOOR'),('pair_balance_progress','PAIR')]:
            for jj,(suffix,feats) in enumerate([('BASE',BASE),('FULL',FULL)]):
                met,_=eval_head(tr,te,label,feats,31000+bi*20+jj+(0 if prefix=='FLOOR' else 10))
                b[f'{prefix}_{suffix}']=met
        blocks.append(b); print(json.dumps({'block':bi,'metrics':{k:v for k,v in b.items() if k.startswith(('FLOOR_','PAIR_'))}},ensure_ascii=False),flush=True)
    summary={k:summarize(blocks,k) for k in ['FLOOR_BASE','FLOOR_FULL','PAIR_BASE','PAIR_FULL']}
    # Final Target teachers: all chronology-eligible Target rows, fixed semantics/hyperparameters.
    floor=e[e.risk_deficit>EPS]; pair=e[e.abs_gap>EPS]
    final={'FLOOR_FULL':model(31991).fit(floor[FULL],floor.floor_repair_progress.astype(int)),'PAIR_FULL':model(31992).fit(pair[FULL],pair.pair_balance_progress.astype(int))}
    transfer=hft_transfer(final)
    floor_gain=summary['FLOOR_FULL']['meanAuc']-summary['FLOOR_BASE']['meanAuc']; pair_gain=summary['PAIR_FULL']['meanAuc']-summary['PAIR_BASE']['meanAuc']
    target_ok=all(summary[k]['meanAuc']>.60 and min(summary[k]['blockAucs'])>.50 for k in ['FLOOR_FULL','PAIR_FULL'])
    no_collapse=(summary['FLOOR_FULL']['worstAuc']>=summary['FLOOR_BASE']['worstAuc']-.01 and summary['PAIR_FULL']['worstAuc']>=summary['PAIR_BASE']['worstAuc']-.01)
    transfer_ok=all((transfer[k]['DIRECT_ECONOMIC_TEACHER']['auc'] or 0)>.50 for k in ['FLOOR_REPAIR_PROGRESS','PAIR_BALANCE_PROGRESS'])
    improves_one=(floor_gain>0 or pair_gain>0)
    if target_ok and no_collapse and transfer_ok and improves_one: status='KEEP'
    elif target_ok and transfer_ok: status='INCONCLUSIVE'
    else: status='REJECT'
    art={'version':'R4_P0B_TARGET_TEACHER_ECONOMIC_PROGRESS_V1','lane':'D_TARGET_TEACHER','status':status,'researchOnly':True,'actionAuthority':False,'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'targetCoverage':{'sourceRows':int(len(d)),'eligibleLabeledRows':int(len(e)),'markets':int(e.market_id.nunique()),'futureHorizonMs':{'median':float(e.future_horizon_ms.median()),'p90':float(e.future_horizon_ms.quantile(.9)),'max':int(e.future_horizon_ms.max())}},'summary':summary,'deltas':{'floorFullMinusBaseMeanAuc':float(floor_gain),'pairFullMinusBaseMeanAuc':float(pair_gain)},'blocks':blocks,'ourRealisticHftTransfer':transfer,'semanticInterpretation':{'M0':'Prefer two explicit portable economic heads (floor-repair progress and pair-balance progress) over treating future Target continuation action as the semantic definition.','Transition':'Economic non-progress should be defined as the complementary risk of these economic heads, not literal root disappearance/stall.','Handoff':'Do not feed this result directly into handoff destination; prior handoff ablations show progress features can hurt destination ranking.'},'files':{'rows':str(ROWS.relative_to(ROOT)).replace('\\','/'),'script':str(Path(__file__).relative_to(ROOT)).replace('\\','/')},'guards':pre['guards']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'targetCoverage':art['targetCoverage'],'summary':summary,'transfer':transfer},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
