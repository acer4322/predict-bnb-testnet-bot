from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,hashlib,statistics
from pathlib import Path
import numpy as np
from sklearn.metrics import r2_score
try:
    from scipy.stats import spearmanr
except Exception:
    spearmanr=None

STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2

EPS=1e-9

def mae(a,b):
    a=np.asarray(a,float);b=np.asarray(b,float);return float(np.mean(np.abs(a-b))) if len(a) else None
def rmse(a,b):
    a=np.asarray(a,float);b=np.asarray(b,float);return float(np.sqrt(np.mean((a-b)**2))) if len(a) else None
def corr(a,b):
    a=np.asarray(a,float);b=np.asarray(b,float)
    if len(a)<2 or np.std(a)<=EPS or np.std(b)<=EPS:return None
    if spearmanr is not None:
        z=spearmanr(a,b).statistic
        return None if not np.isfinite(z) else float(z)
    ra=np.argsort(np.argsort(a)).astype(float); rb=np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra,rb)[0,1])
def stats(x):
    x=np.asarray(x,float)
    return {'mean':float(np.mean(x)),'median':float(np.median(x)),'std':float(np.std(x)),'min':float(np.min(x)),'max':float(np.max(x))} if len(x) else None

def evaluate_round(X,yA,yS,yQ,market,models,round_name):
    X=np.asarray(X,np.float32);yA=np.asarray(yA,int);yS=np.asarray(yS,int);yQ=np.asarray(yQ,float);market=np.asarray(market,int)
    ums=sorted(set(int(x) for x in market));tr=set(ums[:30]);va=set(ums[30:40])
    it=np.where(np.isin(market,list(tr)))[0];iv=np.where(np.isin(market,list(va)))[0]
    posa=it[yA[it]==1];posv=iv[yA[iv]==1]
    tq=np.expm1(yQ[posv])
    pred_log=models['qty'].predict(X[posv]); pred_raw=np.expm1(np.clip(pred_log,0,5)); pred_raw=np.maximum(.01,pred_raw)
    upi=v1.FEATURES.index('up_bid'); dni=v1.FEATURES.index('down_bid')
    p=np.where(yS[posv]==1,X[posv,upi],X[posv,dni]).astype(float)
    legal=np.minimum(12.0,1.0/np.maximum(p,EPS))
    pred_eff=np.minimum(12.0,np.maximum(pred_raw,legal))
    train_qty=np.expm1(yQ[posa]); const=float(np.median(train_qty)) if len(train_qty) else 0.0
    const_raw=np.full(len(posv),const,float); const_eff=np.minimum(12.0,np.maximum(const_raw,legal))
    venue=legal.copy()
    ps=models['side'].predict_proba(X[posv])[:,1]
    side_pred=(ps>=float(models['sideTh'])).astype(int); side_acc=float(np.mean(side_pred==yS[posv])) if len(posv) else None
    correct=(side_pred==yS[posv]); joint_mae=mae(tq[correct],pred_eff[correct]) if np.any(correct) else None
    model_mae=mae(tq,pred_eff); const_mae=mae(tq,const_eff); venue_mae=mae(tq,venue)
    out={
      'round':round_name,'fitMarkets':sorted(tr),'validationMarkets':sorted(va),
      'validationPositiveN':int(len(posv)),'fitPositiveN':int(len(posa)),
      'teacherQty':stats(tq),'predictedQtyRaw':stats(pred_raw),'predictedQtyEffectiveTeacherSide':stats(pred_eff),
      'rawQtyMAE':mae(tq,pred_raw),'rawQtyRMSE':rmse(tq,pred_raw),'rawQtyR2':float(r2_score(tq,pred_raw)) if len(tq)>=2 else None,'rawQtySpearman':corr(tq,pred_raw),'rawQtyBias':float(np.mean(pred_raw-tq)) if len(tq) else None,
      'effectiveQtyMAE':model_mae,'effectiveQtyRMSE':rmse(tq,pred_eff),'effectiveQtyR2':float(r2_score(tq,pred_eff)) if len(tq)>=2 else None,'effectiveQtySpearman':corr(tq,pred_eff),'effectiveQtyBias':float(np.mean(pred_eff-tq)) if len(tq) else None,
      'constantTrainMedianQty':const,'constantEffectiveMAE':const_mae,'venueMinBaselineMAE':venue_mae,
      'modelMAEImprovementVsConstant':(const_mae-model_mae) if const_mae is not None and model_mae is not None else None,
      'modelMAEImprovementVsVenueMin':(venue_mae-model_mae) if venue_mae is not None and model_mae is not None else None,
      'sideThresholdAccuracyOnValidationPositive':side_acc,
      'jointSideCorrectN':int(np.sum(correct)),'jointSideCorrectEffectiveQtyMAE':joint_mae,
      'teacherAtVenueMinFraction':float(np.mean(np.isclose(tq,venue,rtol=1e-6,atol=1e-6))) if len(tq) else None,
      'teacherAtCap12Fraction':float(np.mean(np.isclose(tq,12.0,rtol=1e-6,atol=1e-6))) if len(tq) else None,
      'predAtVenueMinFraction':float(np.mean(np.isclose(pred_eff,venue,rtol=1e-6,atol=1e-6))) if len(tq) else None,
      'predAtCap12Fraction':float(np.mean(np.isclose(pred_eff,12.0,rtol=1e-6,atol=1e-6))) if len(tq) else None,
    }
    return out

def collect_round1(tmp,cohort,traj):
    train=[r for r in cohort if r['split']=='TRAIN40'];X=[];A=[];S=[];Q=[];M=[]
    for i,cr in enumerate(train,1):
      for seed in v1.SEEDS:
        sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
        try:x,a,s,q=sim.run_oracle_collect()
        finally:sim.close()
        X.extend(x);A.extend(a);S.extend(s);Q.extend(q);M.extend([int(cr['marketId'])]*len(x))
      if i%10==0: print(json.dumps({'round1Collect':i,'rows':len(X),'actions':int(sum(A))}),flush=True)
    return X,A,S,Q,M

def collect_round2(tmp,cohort,traj,models1):
    train=[r for r in cohort if r['split']=='TRAIN40'];X=[];A=[];S=[];Q=[];M=[]
    for i,cr in enumerate(train,1):
      for seed in v1.SEEDS:
        sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
        try:x,a,s,q=v2.student_collect(sim,models1)
        finally:sim.close()
        X.extend(x);A.extend(a);S.extend(s);Q.extend(q);M.extend([int(cr['marketId'])]*len(x))
      if i%10==0: print(json.dumps({'round2Collect':i,'rows':len(X),'actions':int(sum(A))}),flush=True)
    return X,A,S,Q,M

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='dagger60_qtycal_'))
    try:
      zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
      X1,A1,S1,Q1,M1=collect_round1(tmp,cohort,traj);models1,off1=v1.fit_models(X1,A1,S1,Q1,M1);e1=evaluate_round(X1,A1,S1,Q1,M1,models1,'ROUND1')
      X2,A2,S2,Q2,M2=collect_round2(tmp,cohort,traj,models1)
      XA=X1+X2;AA=A1+A2;SA=S1+S2;QA=Q1+Q2;MA=M1+M2;models2,off2=v1.fit_models(XA,AA,SA,QA,MA);e2=evaluate_round(XA,AA,SA,QA,MA,models2,'ROUND2_AGGREGATED')
      falsified=(e2['effectiveQtyMAE']>=e2['constantEffectiveMAE']-1e-12) or (e2['effectiveQtyR2'] is not None and e2['effectiveQtyR2']<=0) or (e2['effectiveQtySpearman'] is not None and e2['effectiveQtySpearman']<=0)
      verdict='SOURCE_DOMAIN_QTY_HEAD_FALSIFIED' if falsified else 'SOURCE_DOMAIN_QTY_HEAD_HAS_SKILL'
      out={'version':'DAGGER60_QTY_HEAD_MARKET_DISJOINT_CALIBRATION_V1','verdict':verdict,
           'boundary':['Exact frozen TRAIN40 market-disjoint split used by current fit_models','No TEST20/Fresh101/External24 scoring','No policy behavior change; training/source-domain diagnostic only'],
           'sourceHashes':{'bundle':sha(a.bundle),'runner':sha(__file__),'v1':sha(Path(v1.__file__)),'v2':sha(Path(v2.__file__))},
           'round1OfflineExistingMetrics':off1,'round2OfflineExistingMetrics':off2,'round1QtyAudit':e1,'round2QtyAudit':e2}
      p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8')
      print(json.dumps({'ok':True,'verdict':verdict,'round1':e1,'round2':e2},indent=2),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
