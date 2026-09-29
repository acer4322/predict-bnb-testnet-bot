from __future__ import annotations
import json,lzma,math,sqlite3
from pathlib import Path
import joblib, numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score,average_precision_score,confusion_matrix

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
DB=ROOT/'data/hft_forward_paper_v1.db'; DIR=ROOT/'data/hft_forward_paper_v1/markets'
LAND=[30000,45000,60000,90000,120000]
FEATURES=['orderAgeMs','price','remainingQty','partialFillRatio','activeSameCount','activeOppCount','quoteOffsetTicks','currentBid','currentAsk','currentSpreadTicks','initialDepth','publicCumDepletion','maker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor']

def f(v,d=0.):
    try:x=float(v)
    except:return d
    return x if math.isfinite(x) else d

def rows_for_market(m):
    p=DIR/f'{m}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists():return []
    r=json.load(lzma.open(p,'rt',encoding='utf-8')); states=r.get('orderStateRows') or []
    by={}
    for s in states:
        age=f(s.get('orderAgeMs'),-1); status=str(s.get('hftStatus') or '')
        if age<25000 or status in {'FILLED','CANCELED','EXPIRED'}:continue
        oid=str(s.get('orderId') or '')
        if not oid:continue
        for lm in LAND:
            dist=abs(age-lm)
            if dist<=3000:
                key=(oid,lm);old=by.get(key)
                if old is None or dist<old[0]:by[key]=(dist,s)
    out=[]
    for (oid,lm),(_,s) in by.items():
        pfo=s.get('portfolio') or {}
        x=[f(s.get('orderAgeMs')),f(s.get('price')),f(s.get('remainingQty')),f(s.get('partialFillRatio')),f(s.get('activeSameCount')),f(s.get('activeOppCount')),f(s.get('quoteOffsetTicks')),f(s.get('currentBid')),f(s.get('currentAsk')),f(s.get('currentSpreadTicks')),f(s.get('initialDepth')),f(s.get('publicCumDepletion')),f(pfo.get('maker_abs_net')),f(pfo.get('combined_abs_net')),f(pfo.get('combined_paired_coverage')),f(pfo.get('worst_case_floor'))]
        keep=int(f(s.get('eventualAdditionalFillShares'))>0)
        out.append({'marketId':m,'orderId':oid,'landmarkMs':lm,'x':x,'keep':keep})
    return out

def scores(model,rows):
    if not rows:return {}
    X=np.asarray([r['x'] for r in rows]);y=np.asarray([r['keep'] for r in rows]);pr=model.predict_proba(X)[:,1];pred=(pr>=.5).astype(int)
    cm=confusion_matrix(y,pred,labels=[0,1]);tn,fp,fn,tp=cm.ravel()
    return {'n':len(rows),'keepRate':float(y.mean()),'auc':None if len(set(y))<2 else float(roc_auc_score(y,pr)),'ap':float(average_precision_score(y,pr)),'keepRecall':float(tp/(tp+fn)) if tp+fn else None,'reassessRecall':float(tn/(tn+fp)) if tn+fp else None,'accuracy':float((pred==y).mean()),'meanKeepProbability':float(pr.mean())}

def pairwise_swap(model,rows):
    # Form held-out pairs from the same market/landmark with opposite labels.
    groups={}
    for r in rows:groups.setdefault((r['marketId'],r['landmarkMs']),{0:[],1:[]})[r['keep']].append(r)
    pairs=[]
    for key,g in groups.items():
        n=min(len(g[0]),len(g[1]))
        for i in range(n):pairs.append((g[1][i],g[0][i]))
    if not pairs:return {'pairs':0,'consistency':None}
    ok=0;margins=[]
    for keep,drop in pairs:
        pk=float(model.predict_proba(np.asarray([keep['x']]))[0,1]);pd=float(model.predict_proba(np.asarray([drop['x']]))[0,1])
        ok+=pk>pd;margins.append(pk-pd)
    return {'pairs':len(pairs),'consistency':ok/len(pairs),'meanProbabilityMargin':float(np.mean(margins))}

def main():
    con=sqlite3.connect(DB);ids=[int(r[0]) for r in con.execute("select distinct market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id")];con.close()
    allr=[]
    for m in ids:allr+=rows_for_market(m)
    mids=sorted({r['marketId'] for r in allr});cut=max(1,int(len(mids)*.8));trset=set(mids[:cut]);vaset=set(mids[cut:]);tr=[r for r in allr if r['marketId'] in trset];va=[r for r in allr if r['marketId'] in vaset]
    model=Pipeline([('scale',StandardScaler()),('lr',LogisticRegression(C=0.5,class_weight='balanced',max_iter=1000,random_state=20260825))]);model.fit(np.asarray([r['x'] for r in tr]),np.asarray([r['keep'] for r in tr]))
    vm=scores(model,va);swap=pairwise_swap(model,va)
    by=[]
    for lm in LAND:
        g=[r for r in va if r['landmarkMs']==lm];x=scores(model,g);x['ageSec']=lm/1000;by.append(x)
    gates={'validationAuc':(vm.get('auc') or 0)>=.65,'pairwiseSwapConsistency':(swap.get('consistency') or 0)>=.80,'lateFillKeepRecall':(vm.get('keepRecall') or 0)>=.60,'noFillReassessRecall':(vm.get('reassessRecall') or 0)>=.60}
    art={'version':'R2_MULTICHILD_KEEP_REASSESS_HEAD_V1','features':FEATURES,'landmarksMs':LAND,'model':model,'trainMarkets':sorted(trset),'validationMarkets':sorted(vaset),'authority':'R2_LOGIC_RESEARCH_ONLY_NO_EXECUTOR'}
    joblib.dump(art,OUT/'r2_multichild_keep_reassess_head_v1.joblib')
    rep={'version':'R2_MULTICHILD_KEEP_REASSESS_CURRICULUM_V1','researchOnly':True,'evidenceClass':'HFT_MECHANISM_TRAINING_ONLY_REAL_MARKET_UNCONFIRMED','authority':{'r21ActionAuthority':False,'cancelAuthority':False,'orderMutationAuthority':False,'desiredPortfolioMutationAuthority':False},'train':scores(model,tr),'validation':vm,'validationByAge':by,'pairwiseSwap':swap,'marketSplit':{'train':len(trset),'validation':len(vaset)},'gates':gates,'allPass':all(gates.values()),'decision':'KEEP_R2_CHILD_ASSESSMENT_HEAD_FOR_READ_ONLY_SHADOW_EXAM' if all(gates.values()) else 'REJECT_OR_REVISE_R2_CHILD_ASSESSMENT_HEAD_V1','note':'Prediction only: KEEP_CHILD versus REASSESS_CHILD. No cancellation or venue mutation is permitted.'}
    (OUT/'r2_multichild_keep_reassess_curriculum_v1_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
