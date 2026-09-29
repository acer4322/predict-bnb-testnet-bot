from __future__ import annotations
import argparse,json,math,zipfile
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
EPS=1e-9
FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]
GEOM=[
 'repairPrice','repairQty','expandPrice','expandQty','repairPriceMinusWeakBid','expandPriceMinusExpandBid',
 'repairImmediateDeltaFloor','reexpandImmediateDeltaFloor','immediateReexpandMinusRepairFloor',
 'repairImmediateDeltaBest','reexpandImmediateDeltaBest','immediateReexpandMinusRepairBest',
 'repairImmediateDeltaFavoredPayoff','reexpandImmediateDeltaFavoredPayoff','immediateReexpandMinusRepairFavoredPayoff',
 'repairImmediateDeltaWeakPayoff','reexpandImmediateDeltaWeakPayoff','immediateReexpandMinusRepairWeakPayoff']

def pnl(tm,w):return float(tm['upQty'] if w=='UP' else tm['downQty'])-float(tm['buyNotional'])
def fit(kind,X,y):
    if kind=='FULL':m=ExtraTreesRegressor(n_estimators=300,min_samples_leaf=4,max_features=.75,random_state=260907,n_jobs=1)
    elif kind=='GEOM':m=ExtraTreesRegressor(n_estimators=300,min_samples_leaf=4,max_features=.75,random_state=260907,n_jobs=1)
    elif kind=='RIDGE':m=make_pipeline(StandardScaler(),Ridge(alpha=10.0))
    else:raise ValueError(kind)
    m.fit(X,y);return m
def mdd(xs):
    c=0.;peak=0.;dd=0.
    for x in xs:c+=x;peak=max(peak,c);dd=max(dd,peak-c)
    return dd
def worst_mean(xs,frac=.2):
    a=sorted(xs);n=max(1,int(math.ceil(len(a)*frac)));return sum(a[:n])/n

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_CORPUS_V2_DIRECTION_ALIGNED_20260907.json');ap.add_argument('--forks',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_H100_MERGED_V1_20260907.json');ap.add_argument('--bundle',default='data/research/r4_v0/p0_provenance_v1/v16_consumed_holdout100_bundle.zip');ap.add_argument('--output',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_ROLE_SWITCH_DIRECT_PNL_VALUE_V1_20260907.json');a=ap.parse_args()
    c=json.load(open(a.corpus,encoding='utf-8'));f=json.load(open(a.forks,encoding='utf-8')); cr={(int(r['marketId']),int(r['t'])):r for r in c['rows']}; rows=sorted(f['rows'],key=lambda r:(int(r['marketId']),int(r['t'])))
    with zipfile.ZipFile(a.bundle) as z:win={int(x['marketId']):str(x['winner']).upper() for x in json.loads(z.read('cohort.json'))['rows']}
    feats=list(c['rows'][0]['features']);oos=[];foldrep=[]
    for fi,(a0,a1,b0,b1) in enumerate(FOLDS,1):
        tr=rows[a0:a1];te=rows[b0:b1]
        def X(rr,fs):return np.array([[float(cr[(int(r['marketId']),int(r['t']))]['features'][k]) for k in fs] for r in rr])
        def Y(rr):
            out=[]
            for r in rr:
                w=win[int(r['marketId'])];tm=r['terminalMetrics'];out.append(pnl(tm['NEXT_REEXPAND'],w)-pnl(tm['NEXT_REPAIR'],w))
            return np.array(out)
        ytr=Y(tr);yte=Y(te);models={
          'FULL':fit('FULL',X(tr,feats),ytr),
          'GEOM':fit('GEOM',X(tr,GEOM),ytr),
          'RIDGE':fit('RIDGE',X(tr,feats),ytr)}
        preds={'FULL':models['FULL'].predict(X(te,feats)),'GEOM':models['GEOM'].predict(X(te,GEOM)),'RIDGE':models['RIDGE'].predict(X(te,feats))}
        foldrep.append({'fold':fi,'train':[a0,a1],'test':[b0,b1],'trainNonTie':int(np.sum(np.abs(ytr)>EPS)),'testNonTie':int(np.sum(np.abs(yte)>EPS))})
        for j,r in enumerate(te):
            w=win[int(r['marketId'])];tm=r['terminalMetrics'];native=str(r['stateSpec']['nativeClass']);bp=pnl(tm['NATIVE'],w);rrp=pnl(tm['NEXT_REPAIR'],w);eep=pnl(tm['NEXT_REEXPAND'],w)
            rec={'fold':fi,'marketId':int(r['marketId']),'winnerPostHoc':w,'nativeClass':native,'trueDeltaPnlReexpandMinusRepair':float(yte[j]),'baselinePnl':bp,'repairPnl':rrp,'reexpandPnl':eep,'baselineFills':int(tm['NATIVE']['fills']),'pred':{}}
            for k in preds:
                pv=float(preds[k][j]);choice='REEXPAND' if pv>EPS else ('REPAIR' if pv<-EPS else native);cp=eep if choice=='REEXPAND' else rrp;cf=int(tm['NEXT_REEXPAND']['fills'] if choice=='REEXPAND' else tm['NEXT_REPAIR']['fills'])
                rec['pred'][k]={'value':pv,'choice':choice,'changed':choice!=native,'candidatePnl':cp,'deltaVsNative':cp-bp,'candidateFills':cf}
            oos.append(rec)
    summaries={}
    for model in ('FULL','GEOM','RIDGE'):
        yy=np.array([r['trueDeltaPnlReexpandMinusRepair'] for r in oos]);pp=np.array([r['pred'][model]['value'] for r in oos]);non=np.abs(yy)>EPS
        sign=float(np.mean(np.sign(pp[non])==np.sign(yy[non]))) if np.any(non) else None
        aff=[r for r in oos if r['pred'][model]['changed']];ad=[r['pred'][model]['deltaVsNative'] for r in aff];base=[r['baselinePnl'] for r in oos];cand=[r['pred'][model]['candidatePnl'] for r in oos]
        imp=sum(x>EPS for x in ad);wor=sum(x<-EPS for x in ad);tie=len(ad)-imp-wor
        bw=worst_mean(base);cw=worst_mean(cand);bd=mdd(base);cd=mdd(cand);bmin=min(base);cmin=min(cand)
        gates={'affectedImprovementRate70':bool(aff and imp/len(aff)>=.70),'totalPnlImproves':bool(sum(cand)>sum(base)+EPS),'winRateNonWorse':bool(sum(x>EPS for x in cand)>=sum(x>EPS for x in base)),'worstMarketWithin10Pct':bool(cmin>=bmin-.10*abs(bmin)-EPS),'worst20Within10Pct':bool(cw>=bw-.10*abs(bw)-EPS),'maxDDWithin110Pct':bool(cd<=1.10*bd+EPS),'activity80Pct':bool(sum(r['pred'][model]['candidateFills'] for r in oos)>=.8*sum(r['baselineFills'] for r in oos)-EPS)}
        summaries[model]={'maeDeltaPnl':float(np.mean(np.abs(yy-pp))),'signAccuracyNonTie':sign,'nonTieMarkets':int(np.sum(non)),'affectedMarkets':len(aff),'affectedImproved':imp,'affectedWorsened':wor,'affectedTied':tie,'affectedImprovementRate':imp/len(aff) if aff else None,'baselineTotalPnl':sum(base),'candidateTotalPnl':sum(cand),'deltaTotalPnl':sum(cand)-sum(base),'baselineWinRate':sum(x>EPS for x in base)/len(base),'candidateWinRate':sum(x>EPS for x in cand)/len(cand),'baselineWorst':bmin,'candidateWorst':cmin,'baselineWorst20Mean':bw,'candidateWorst20Mean':cw,'baselineMaxDD':bd,'candidateMaxDD':cd,'gates':gates,'all70_30GatesPass':all(gates.values())}
    out={'version':'MANAGEMENT_MAINLINE_ROLE_SWITCH_DIRECT_PNL_VALUE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'folds':foldrep,'summary':summaries,'rows':oos,'boundary':['winner/settlement used only to create offline action-value label and posthoc evaluation; never feature','strict-past V3B features only','fixed ExtraTrees FULL primary, geometry comparator, Ridge robustness; no hyperparameter or threshold sweep','choice threshold is natural zero expected PnL difference','single first-eligible intervention per market; current V3B suffix','whole-market repeated controller not promoted from this test','70/30 gate preregistered separately','NEW24-B untouched/no dream fill/no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'summary':summaries},ensure_ascii=False))
if __name__=='__main__':main()
