from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np

import r4_hourly_prepare_and_train_v1 as prep

ROOT=Path(__file__).resolve().parents[1]
R4=ROOT/'data'/'research'/'r4_v0'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_SELECTIVE_PRESERVATION_CONTROL_V1'


def finite(x,d=0.0):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except Exception:return d


def floor_from_row(r):
    return finite(r['floor'])


def side_cost(r,side):
    q=finite(r[side.lower()])
    a=finite(r['avg_'+side.lower()])
    return q*a


def counterfactual_path(traj,j,horizon_steps=6,stress_realization=1.0):
    """Suppress only NEW quantity on the initial surplus side after warning.
    Preserve weak-side additions. For execution stress, weak-side additions realize only
    stress_realization fraction while suppressed surplus additions remain zero.
    Uses future rows only for offline counterfactual evaluation, never runtime input.
    """
    cur=traj[j]
    up0,down0=finite(cur['up']),finite(cur['down'])
    cu0,cd0=side_cost(cur,'UP'),side_cost(cur,'DOWN')
    if abs(up0-down0)<1e-9:
        return None
    surplus='UP' if up0>down0 else 'DOWN'
    floors=[]
    end=min(len(traj),j+horizon_steps+1)
    for k in range(j,end):
        r=traj[k]
        up,down=finite(r['up']),finite(r['down'])
        cu,cd=side_cost(r,'UP'),side_cost(r,'DOWN')
        if surplus=='UP':
            # remove any new UP expansion; weak-side DOWN continuation may be stressed
            up_cf=up0; cu_cf=cu0
            dd=max(0.0,down-down0); dcd=max(0.0,cd-cd0)
            down_cf=down0+stress_realization*dd; cd_cf=cd0+stress_realization*dcd
        else:
            down_cf=down0; cd_cf=cd0
            du=max(0.0,up-up0); dcu=max(0.0,cu-cu0)
            up_cf=up0+stress_realization*du; cu_cf=cu0+stress_realization*dcu
        floors.append(min(up_cf,down_cf)-(cu_cf+cd_cf))
    return floors


def summarize(vals):
    a=np.asarray(vals,float)
    return {'n':int(len(a)),'mean':float(np.mean(a)) if len(a) else None,'median':float(np.median(a)) if len(a) else None,
            'p10':float(np.quantile(a,.1)) if len(a) else None,'p90':float(np.quantile(a,.9)) if len(a) else None}


def main():
    _,rows,by=prep.load_target(500)
    art=joblib.load(R4/'r4_base_preservation_teacher_v1.joblib')
    model=art['model']; feats=art['features']
    # reproduce chronological preservation test split exactly
    pos=[r for r in rows if r['gross']>1e-9 and r['floor']>0]
    markets=sorted(set(int(r['market_id']) for r in pos)); cut=max(1,int(len(markets)*.8)); testm=set(markets[cut:])
    diffs=[]; actualmins=[]; cfmins=[]; improved=0; protected=0; warned=0; rows_used=0
    stress_diffs=[]; stress_cfmins=[]; stress_protected=0
    per_market={}
    realization=0.6811984126984127  # frozen Echtgeld calibration: environment stress only, not action tuning
    for mid in sorted(testm):
        traj=by[mid]; md=[]
        for j,r in enumerate(traj):
            if r['gross']<=1e-9 or r['floor']<=0: continue
            x=np.asarray([[finite(r.get(f)) for f in feats]],float)
            pred=float(model.predict(x)[0])
            if pred>0: continue
            warned+=1
            path=traj[j:min(len(traj),j+7)]
            if not path: continue
            cf=counterfactual_path(traj,j,6,1.0)
            scf=counterfactual_path(traj,j,6,realization)
            if not cf or not scf: continue
            amin=min(floor_from_row(z) for z in path); cmin=min(cf); smin=min(scf)
            d=cmin-amin; sd=smin-amin
            rows_used+=1; diffs.append(d); actualmins.append(amin); cfmins.append(cmin); stress_diffs.append(sd); stress_cfmins.append(smin)
            improved += d>1e-9; protected += (amin<=0 and cmin>0); stress_protected += (amin<=0 and smin>0)
            md.append({'checkpointMs':int(r['checkpoint_ms']),'currentFloor':finite(r['floor']),'predMinFloorPerGross30s':pred,
                       'actualMinFloor30s':amin,'selectiveCfMinFloor30s':cmin,'stressCfMinFloor30s':smin,'deltaMinFloor':d,'stressDeltaMinFloor':sd})
        if md: per_market[str(mid)]={'rows':len(md),'medianDeltaMinFloor':float(np.median([z['deltaMinFloor'] for z in md])),
                                    'protectedRows':sum(z['actualMinFloor30s']<=0 and z['selectiveCfMinFloor30s']>0 for z in md)}
    report={
      'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),
      'candidate':'When preservation warning is active, suppress only new initial-surplus-side expansion while allowing weak-side/base-deepening continuation.',
      'runtimeInputs':'current strict-past state + preservation warning only; future path used offline for evaluation only',
      'dataset':{'targetMarkets':500,'chronologicalPreservationTestMarkets':len(testm),'warnedRows':warned,'evaluatedRows':rows_used,'special20260816Sealed':True},
      'primary':{'deltaMinFloor30s':summarize(diffs),'actualMinFloor30s':summarize(actualmins),'selectiveCfMinFloor30s':summarize(cfmins),
                 'fractionRowsImproved':improved/max(rows_used,1),'floorLossRowsProtectedToPositive':protected},
      'executionStress':{'weakSideRealization':realization,'source':'FROZEN_ECHTGELD_ENVIRONMENT_CALIBRATION_ONLY','deltaMinFloor30s':summarize(stress_diffs),
                         'stressCfMinFloor30s':summarize(stress_cfmins),'floorLossRowsProtectedToPositive':stress_protected},
      'perMarket':per_market,
      'guards':{'liveAuthority':False,'r3Modified':False,'newEchtgeldUsed':False,'thresholdSweep':False,'futureRuntimeLeak':False},
    }
    # predeclared keep gate: median floor improves >0, >=55% warned rows improve, and stressed median does not reverse.
    med=report['primary']['deltaMinFloor30s']['median'] or 0.0; smed=report['executionStress']['deltaMinFloor30s']['median'] or 0.0
    frac=report['primary']['fractionRowsImproved']
    report['decision']='KEEP_SIGNAL' if med>0 and frac>=.55 and smed>=0 else 'REJECTED'
    out=R4/'hourly'/f"r4_hourly_experiment_{datetime.now(TZ).strftime('%Y%m%d_%H%M')}_selective_preservation_control_v1.json"
    out.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'decision':report['decision'],'rows':rows_used,
                      'medianDeltaMinFloor30s':med,'fractionImproved':frac,'protectedRows':protected,'stressMedianDelta':smed,'stressProtectedRows':stress_protected}))

if __name__=='__main__':main()
