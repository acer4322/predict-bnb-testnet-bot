from __future__ import annotations
import json,lzma,math,sqlite3,bisect
from pathlib import Path
import joblib,numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score,average_precision_score,confusion_matrix
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0';DB=ROOT/'data/hft_forward_paper_v1.db';DIR=ROOT/'data/hft_forward_paper_v1/markets'
LAND=[30000,45000,60000,90000,120000]
FEATURES=['orderAgeMs','price','remainingQty','partialFillRatio','activeSameCount','activeOppCount','quoteOffsetTicks','currentBid','currentAsk','currentSpreadTicks','initialDepth','publicCumDepletion','maker_abs_net','combined_abs_net','combined_paired_coverage','worst_case_floor','ageVsOldestLiveMs','ageVsYoungestLiveMs','sameSideOldestFlag','allSideOldestFlag','priceVsSameSideMean','priceVsBidTicks','reasonHazard','reasonBurst','r2PassiveRepair','r2PassiveMaintain','r2ActiveIntervention','childSideAlignedDirection','childSideAlignedMakerDeficit','activeMakerOrdersDecision']
def f(v,d=0.):
    try:x=float(v)
    except:return d
    return x if math.isfinite(x) else d
def nearest_decision(decisions,t):
    if not decisions:return None
    times=[int(x.get('decisionMs') or 0) for x in decisions];i=bisect.bisect_right(times,int(t))-1
    return decisions[i] if i>=0 else None
def rows_for_market(m):
    path=DIR/f'{m}_r2_hft_closed_loop_v1.json.xz'
    if not path.exists():return []
    r=json.load(lzma.open(path,'rt',encoding='utf-8'));states=r.get('orderStateRows') or [];meta=r.get('orderMeta') or {};dec=sorted(r.get('decisionRows') or [],key=lambda x:int(x.get('decisionMs') or 0))
    checkpoint={}
    for s in states:checkpoint.setdefault(int(s.get('checkpointMs') or 0),[]).append(s)
    by={}
    for s in states:
        age=f(s.get('orderAgeMs'),-1);status=str(s.get('hftStatus') or '');oid=str(s.get('orderId') or '')
        if age<25000 or status in {'FILLED','CANCELED','EXPIRED'} or not oid:continue
        for lm in LAND:
            dist=abs(age-lm)
            if dist<=3000:
                k=(oid,lm);old=by.get(k)
                if old is None or dist<old[0]:by[k]=(dist,s)
    out=[]
    for (oid,lm),(_,s) in by.items():
        t=int(s.get('checkpointMs') or 0);side=str(s.get('side') or '').upper();pfo=s.get('portfolio') or {};brows=checkpoint.get(t) or []
        live=[x for x in brows if str(x.get('hftStatus') or '') not in {'FILLED','CANCELED','EXPIRED'} and f(x.get('remainingQty'))>0]
        ages=[f(x.get('orderAgeMs')) for x in live] or [f(s.get('orderAgeMs'))];same=[x for x in live if str(x.get('side') or '').upper()==side];sameages=[f(x.get('orderAgeMs')) for x in same] or [f(s.get('orderAgeMs'))];sameprices=[f(x.get('price')) for x in same] or [f(s.get('price'))]
        age=f(s.get('orderAgeMs'));price=f(s.get('price'));bid=f(s.get('currentBid'));om=meta.get(oid) or {};reason=str(om.get('reason') or '')
        d=nearest_decision(dec,t) or {};act=str(d.get('desiredPortfolioAction') or '');direction=((d.get('direction') or {}).get('side') or '')
        maker_net=f(pfo.get('maker_net')); deficit_side='DOWN' if maker_net>0 else ('UP' if maker_net<0 else '')
        base=[age,price,f(s.get('remainingQty')),f(s.get('partialFillRatio')),f(s.get('activeSameCount')),f(s.get('activeOppCount')),f(s.get('quoteOffsetTicks')),bid,f(s.get('currentAsk')),f(s.get('currentSpreadTicks')),f(s.get('initialDepth')),f(s.get('publicCumDepletion')),f(pfo.get('maker_abs_net')),f(pfo.get('combined_abs_net')),f(pfo.get('combined_paired_coverage')),f(pfo.get('worst_case_floor'))]
        extra=[age-max(ages),age-min(ages),float(age>=max(sameages)-1e-6),float(age>=max(ages)-1e-6),price-float(np.mean(sameprices)),(bid-price)/.01 if bid and price else 0.,float('HAZARD' in reason),float('BURST' in reason),float(act=='PASSIVE_REPAIR'),float(act=='PASSIVE_MAINTAIN'),float(act=='ACTIVE_INTERVENTION'),float(side==str(direction).upper()),float(side==deficit_side),f(d.get('activeMakerOrders'))]
        out.append({'marketId':m,'orderId':oid,'landmarkMs':lm,'x':base+extra,'keep':int(f(s.get('eventualAdditionalFillShares'))>0)})
    return out
def scores(model,rows):
    if not rows:return {}
    X=np.asarray([r['x'] for r in rows]);y=np.asarray([r['keep'] for r in rows]);pr=model.predict_proba(X)[:,1];pred=(pr>=.5).astype(int);tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1]).ravel()
    return {'n':len(rows),'keepRate':float(y.mean()),'auc':None if len(set(y))<2 else float(roc_auc_score(y,pr)),'ap':float(average_precision_score(y,pr)),'keepRecall':float(tp/(tp+fn)) if tp+fn else None,'reassessRecall':float(tn/(tn+fp)) if tn+fp else None,'accuracy':float((pred==y).mean()),'meanKeepProbability':float(pr.mean())}
def pairwise(model,rows):
    groups={}
    for r in rows:groups.setdefault((r['marketId'],r['landmarkMs']),{0:[],1:[]})[r['keep']].append(r)
    pairs=[]
    for g in groups.values():
        n=min(len(g[0]),len(g[1]));pairs += [(g[1][i],g[0][i]) for i in range(n)]
    if not pairs:return {'pairs':0,'consistency':None}
    good=0;m=[]
    for k,d in pairs:
        pk=float(model.predict_proba([k['x']])[0,1]);pd=float(model.predict_proba([d['x']])[0,1]);good+=pk>pd;m.append(pk-pd)
    return {'pairs':len(pairs),'consistency':good/len(pairs),'meanProbabilityMargin':float(np.mean(m))}
def main():
    con=sqlite3.connect(DB);ids=[int(x[0]) for x in con.execute("select distinct market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id")];con.close();allr=[]
    for m in ids:allr+=rows_for_market(m)
    mids=sorted({r['marketId'] for r in allr});cut=max(1,int(len(mids)*.8));trs=set(mids[:cut]);vas=set(mids[cut:]);tr=[r for r in allr if r['marketId'] in trs];va=[r for r in allr if r['marketId'] in vas]
    model=Pipeline([('scale',StandardScaler()),('lr',LogisticRegression(C=.5,class_weight='balanced',max_iter=1000,random_state=20260825))]);model.fit(np.asarray([r['x'] for r in tr]),np.asarray([r['keep'] for r in tr]));vm=scores(model,va);pw=pairwise(model,va);by=[]
    for lm in LAND:
        g=[r for r in va if r['landmarkMs']==lm];z=scores(model,g);z['ageSec']=lm/1000;by.append(z)
    gates={'validationAuc':(vm.get('auc') or 0)>=.65,'pairwiseSwapConsistency':(pw.get('consistency') or 0)>=.80,'lateFillKeepRecall':(vm.get('keepRecall') or 0)>=.60,'noFillReassessRecall':(vm.get('reassessRecall') or 0)>=.60};art={'version':'R2_MULTICHILD_KEEP_REASSESS_HEAD_V2','features':FEATURES,'landmarksMs':LAND,'model':model,'trainMarkets':sorted(trs),'validationMarkets':sorted(vas),'authority':'READ_ONLY_R2_LOGIC_HEAD'};joblib.dump(art,OUT/'r2_multichild_keep_reassess_head_v2.joblib')
    rep={'version':'R2_MULTICHILD_KEEP_REASSESS_CURRICULUM_V2','researchOnly':True,'evidenceClass':'HFT_MECHANISM_TRAINING_ONLY_REAL_MARKET_UNCONFIRMED','changeFromV1':'Added strict-past multi-child relative context and current Frozen-R2 decision context; no threshold/model sweep.','train':scores(model,tr),'validation':vm,'validationByAge':by,'pairwiseSwap':pw,'gates':gates,'allPass':all(gates.values()),'decision':'KEEP_FOR_R2_READ_ONLY_SHADOW_EXAM' if all(gates.values()) else 'REJECT_OR_REVISE_V2','authority':{'cancel':False,'orderMutation':False,'desiredMutation':False}};(OUT/'r2_multichild_keep_reassess_curriculum_v2_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
