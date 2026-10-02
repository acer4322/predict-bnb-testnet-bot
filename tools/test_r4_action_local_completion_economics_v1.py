from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
PAIR=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
CHILD=D/'pair_completion_child_fill_labels_canonical_v1.jsonl'
OOF=D/'pair_completion_fill_expert_oof_v1.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_hourly_experiment_20260826_1435_action_local_completion_economics_v1.json'

def f(v, default=float('nan')):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except Exception: return default

def main():
    pair={int(r['marketId']):r for r in (json.loads(x) for x in PAIR.read_text(encoding='utf-8').splitlines() if x.strip())}
    child={int(r['marketId']):r for r in (json.loads(x) for x in CHILD.read_text(encoding='utf-8').splitlines() if x.strip())}
    oof=pd.read_csv(OOF)
    rows=[]
    for _,z in oof.iterrows():
        mid=int(z.marketId); pr=pair.get(mid); cr=child.get(mid)
        if not pr or not cr or not cr.get('childKeepLabels'): continue
        feat=pr.get('features') or {}; lab=cr['childKeepLabels']
        price=f(feat.get('workingRecoveryPrice'))
        rem=f(feat.get('workingRecoveryRemainingQty'))
        if not (math.isfinite(price) and math.isfinite(rem) and rem>0): continue
        p3=f(z.get('pFill3s')); p5=f(z.get('pFill5s'))
        if not (math.isfinite(p3) and math.isfinite(p5)): continue
        actual3=max(0.0,f(lab.get('fillShares3s'),0.0))
        actual5=max(0.0,f(lab.get('fillShares5s'),0.0))
        per_share=max(0.0,1.0-price)  # maker weak-side fill raises worst-case floor by 1-price per confirmed share, before any later actions
        pred3=p3*rem*per_share; pred5=p5*rem*per_share
        act3=actual3*per_share; act5=actual5*per_share
        rows.append({'marketId':mid,'fold':str(z.fold),'p3':p3,'p5':p5,'price':price,'remaining':rem,
                     'predFloorRecovery3s':pred3,'actualFloorRecovery3s':act3,
                     'predFloorRecovery5s':pred5,'actualFloorRecovery5s':act5,
                     'actualFillShares3s':actual3,'actualFillShares5s':actual5})
    df=pd.DataFrame(rows)
    def metrics(x):
        if len(x)<3:return {'n':int(len(x))}
        def rho(a,b):
            r=spearmanr(x[a],x[b]).statistic
            return None if not math.isfinite(float(r)) else float(r)
        return {
            'n':int(len(x)),
            'markets':int(x.marketId.nunique()),
            'positiveActual5s':int((x.actualFloorRecovery5s>0).sum()),
            'actual5sRate':float((x.actualFloorRecovery5s>0).mean()),
            'spearmanPredVsActualFloorRecovery3s':rho('predFloorRecovery3s','actualFloorRecovery3s'),
            'spearmanPredVsActualFloorRecovery5s':rho('predFloorRecovery5s','actualFloorRecovery5s'),
            'maeFloorRecovery5s':float(mean_absolute_error(x.actualFloorRecovery5s,x.predFloorRecovery5s)),
            'meanPredFloorRecovery5s':float(x.predFloorRecovery5s.mean()),
            'meanActualFloorRecovery5s':float(x.actualFloorRecovery5s.mean()),
            'medianPredFloorRecovery5s':float(x.predFloorRecovery5s.median()),
            'medianActualFloorRecovery5s':float(x.actualFloorRecovery5s.median()),
        }
    groups={
        'allOOF':metrics(df),
        'trainOOF':metrics(df[df.fold.astype(str).str.startswith('oof')]),
        'frozenValidation':metrics(df[df.fold=='frozen_val']),
        'forwardOos':metrics(df[df.fold=='forward']),
        'frozenPlusForward':metrics(df[df.fold.isin(['frozen_val','forward'])]),
    }
    # Predeclared signal rule: action-local economic quantity must rank realized 5s floor recovery on independent frozen+forward rows,
    # and must not rely on a tuned threshold. Small n is acknowledged; require rho>=0.45 and same positive direction in train OOF.
    ind=groups['frozenPlusForward']; tr=groups['trainOOF']
    rho_ind=ind.get('spearmanPredVsActualFloorRecovery5s'); rho_tr=tr.get('spearmanPredVsActualFloorRecovery5s')
    keep=bool(rho_ind is not None and rho_tr is not None and rho_ind>=0.45 and rho_tr>0)
    report={
      'version':'R4_ACTION_LOCAL_COMPLETION_ECONOMICS_V1',
      'status':'KEEP_SIGNAL' if keep else 'REJECTED',
      'researchOnly':True,
      'candidate':'For a specific pending weak-side Maker order, convert strict-past OOF completion probability into expected short-horizon safe-base recovery: p(fill_h)*remaining_shares*(1-maker_price).',
      'semanticKeys':['action-local completion economics','pending maker completion probability','expected floor recovery','weak-side maker order value'],
      'source':{
        'pairCurriculum':str(PAIR.relative_to(ROOT)),
        'childLabels':str(CHILD.relative_to(ROOT)),
        'oofPredictions':str(OOF.relative_to(ROOT)),
        'note':'Existing OOF fill predictions are expanding-window strict-past. Echtgeld is not used for fitting or thresholds.'
      },
      'formula':'expected_floor_recovery_h = pFill_h * remaining_qty * (1 - maker_price)',
      'label':'realized_confirmed_fill_shares_h * (1 - maker_price)',
      'metrics':groups,
      'acceptanceRule':'KEEP only if frozenValidation+forwardOOS 5s Spearman >= 0.45 and train-OOF direction is positive; no threshold sweep.',
      'guards':['No winner or future Target state as runtime input.','Future fill shares are evaluation labels only.','No Echtgeld fitting.','No action authority.','No threshold sweep.'],
      'interpretation':'Action-local expected floor recovery is a more local execution-economic quantity than generic recovery-path-break classification.' if keep else 'This probability-to-floor-recovery proxy is not stable enough to use as an R4 research expert; do not tune its threshold.',
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)),'status':report['status'],'metrics':groups},ensure_ascii=False))
if __name__=='__main__': main()
