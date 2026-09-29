from __future__ import annotations
import json,joblib,numpy as np,sys,sqlite3
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.train_r2_multichild_keep_reassess_curriculum_v2 import rows_for_market,DB,OUT,FEATURES

def make_pairs(rows):
    g={}
    for r in rows:g.setdefault((r['marketId'],r['landmarkMs']),{0:[],1:[]})[r['keep']].append(r)
    pairs=[]
    for grp in g.values():
        n=min(len(grp[0]),len(grp[1]))
        for i in range(n):pairs.append((grp[1][i],grp[0][i]))
    return pairs

def main():
    con=sqlite3.connect(DB);ids=[int(x[0]) for x in con.execute("select distinct market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id")];con.close();allr=[]
    for m in ids:allr+=rows_for_market(m)
    mids=sorted({r['marketId'] for r in allr});cut=max(1,int(len(mids)*.8));trs=set(mids[:cut]);vas=set(mids[cut:]);tr=[r for r in allr if r['marketId'] in trs];va=[r for r in allr if r['marketId'] in vas]
    scaler=StandardScaler().fit(np.asarray([r['x'] for r in tr]));tp=make_pairs(tr);vp=make_pairs(va)
    X=[];y=[]
    for k,d in tp:
        a=scaler.transform([k['x']])[0];b=scaler.transform([d['x']])[0];X.append(a-b);y.append(1);X.append(b-a);y.append(0)
    rank=LogisticRegression(C=.5,fit_intercept=False,max_iter=1000,random_state=20260825).fit(np.asarray(X),np.asarray(y))
    def score(r):return float(np.dot(scaler.transform([r['x']])[0],rank.coef_[0]))
    good=0;marg=[]
    for k,d in vp:
        z=score(k)-score(d);good+=z>0;marg.append(z)
    consistency=good/len(vp) if vp else None;ks=[score(r) for r in va if r['keep']==1];ds=[score(r) for r in va if r['keep']==0]
    rep={'version':'R2_MULTICHILD_PAIRWISE_RANKER_V3','researchOnly':True,'evidenceClass':'HFT_MECHANISM_TRAINING_ONLY_REAL_MARKET_UNCONFIRMED','trainingObjective':'Within the same market/landmark, rank eventual-late-fill child above never-late-fill child.','trainPairs':len(tp),'validationPairs':len(vp),'validationPairwiseConsistency':consistency,'meanValidationMargin':None if not marg else float(np.mean(marg)),'medianKeepScore':None if not ks else float(np.median(ks)),'medianReassessScore':None if not ds else float(np.median(ds)),'gate':{'pairwiseConsistencyMin':0.80,'pass':bool(consistency is not None and consistency>=.80)},'decision':'KEEP_PAIRWISE_R2_CHILD_RANKER_FOR_READ_ONLY_SHADOW' if consistency is not None and consistency>=.80 else 'REJECT_PAIRWISE_RANKER_V3','authority':{'cancel':False,'orderMutation':False,'desiredMutation':False},'note':'Ranker only establishes relative KEEP priority among simultaneous children. No absolute cancel threshold is defined.'}
    joblib.dump({'version':'R2_MULTICHILD_PAIRWISE_RANKER_V3','features':FEATURES,'scaler':scaler,'ranker':rank,'trainMarkets':sorted(trs),'validationMarkets':sorted(vas)},OUT/'r2_multichild_pairwise_ranker_v3.joblib');(OUT/'r2_multichild_pairwise_ranker_v3_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
