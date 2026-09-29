from __future__ import annotations

import json, math, statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
SRC = D / 'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
OUT = D / 'execution_mpc_v0_oracle_report.json'

TRACK_KEYS = ['deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s']
COST_KEYS = ['deltaCompletionCost5s','deltaCompletionCost10s','deltaCompletionCost20s']

CONFIGS = [
    {'name':'TRACK_ONLY','trackW':1.0,'costW':0.0,'queueW':0.0,'urgencyW':0.0,'deadband':0.25},
    {'name':'TRACK_COST_BALANCED','trackW':1.0,'costW':1.0,'queueW':0.0,'urgencyW':0.0,'deadband':0.25},
    {'name':'TRACK_COST_QUEUE_LIGHT','trackW':1.0,'costW':1.0,'queueW':0.25,'urgencyW':0.0,'deadband':0.25},
    {'name':'TRACK_COST_URGENCY_LIGHT','trackW':1.0,'costW':1.0,'queueW':0.0,'urgencyW':0.50,'deadband':0.25},
    {'name':'MPC_BALANCED','trackW':1.0,'costW':1.0,'queueW':0.25,'urgencyW':0.50,'deadband':0.50},
    {'name':'MPC_WIDE_WAIT','trackW':1.0,'costW':1.0,'queueW':0.25,'urgencyW':0.50,'deadband':0.75},
    {'name':'MPC_COST_HEAVY','trackW':1.0,'costW':2.0,'queueW':0.25,'urgencyW':0.50,'deadband':0.50},
]


def f(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def robust_scale(vals:list[float])->float:
    z=[abs(float(x)) for x in vals if math.isfinite(float(x)) and abs(float(x))>1e-12]
    if not z: return 1.0
    m=statistics.median(z)
    return max(float(m),1e-9)


def truth3(row:dict[str,Any])->str:
    lab=str(row.get('paretoLabel'))
    if lab=='REPLACE_DOMINATES': return 'REPLACE'
    if lab=='KEEP_DOMINATES': return 'KEEP'
    return 'WAIT'


def queue_value(row:dict[str,Any])->float:
    x=row.get('features') or {}
    if float(x.get('workingRecoveryExists') or 0.0) < 0.5: return 0.0
    age=max(0.0,f(x.get('workingRecoveryAgeMs')))
    off=f(x.get('workingRecoveryOffsetTicks'))
    rem=f(x.get('workingRecoveryRemainingQty'))
    if not math.isfinite(age): age=0.0
    if not math.isfinite(off): off=20.0
    if not math.isfinite(rem): rem=18.0
    # Heuristic value of keeping an already-resting child: older queue position helps,
    # but value decays quickly when the quote is far behind current best.
    return min(age/5000.0,1.0) * math.exp(-max(off,0.0)/5.0) * min(max(rem/18.0,0.0),1.0)


def urgency(row:dict[str,Any])->float:
    sec=f((row.get('features') or {}).get('secondsLeft'))
    if not math.isfinite(sec): return 0.0
    return min(max((300.0-sec)/300.0,0.0),1.0)


def score(row:dict[str,Any],cfg:dict[str,float],scales:dict[str,float])->tuple[float,dict[str,float]]:
    t=[]; c=[]
    for k in TRACK_KEYS:
        v=f(row.get(k))
        if math.isfinite(v): t.append(v/scales[k])
    for k in COST_KEYS:
        v=f(row.get(k))
        if math.isfinite(v): c.append(v/scales[k])
    ts=sum(t)/len(t) if t else 0.0
    cs=sum(c)/len(c) if c else 0.0
    qv=queue_value(row)
    urg=urgency(row)
    # Negative score favors route replacement. Queue value is a positive cancel penalty.
    s=cfg['trackW']*ts*(1.0+cfg['urgencyW']*urg)+cfg['costW']*cs+cfg['queueW']*qv
    return s,{'track':ts,'cost':cs,'queue':qv,'urgency':urg}


def pred3(s:float,band:float)->str:
    if s < -band: return 'REPLACE'
    if s > band: return 'KEEP'
    return 'WAIT'


def eval_rows(rows:list[dict[str,Any]],cfg:dict[str,float],scales:dict[str,float])->dict[str,Any]:
    rec=[]
    for r in rows:
        s,parts=score(r,cfg,scales); p=pred3(s,cfg['deadband']); t=truth3(r)
        rec.append((r,p,t,s,parts))
    n=len(rec)
    exact=sum(p==t for _,p,t,_,_ in rec)
    premature=sum(t=='WAIT' and p!='WAIT' for _,p,t,_,_ in rec)
    missed=sum(t!='WAIT' and p=='WAIT' for _,p,t,_,_ in rec)
    wrong=sum(t in {'KEEP','REPLACE'} and p in {'KEEP','REPLACE'} and t!=p for _,p,t,_,_ in rec)
    chosen_rep=[r for r,p,_,_,_ in rec if p=='REPLACE' and r.get('deltaPnlDiagnostic') is not None]
    pnl=sum(float(r['deltaPnlDiagnostic']) for r in chosen_rep)
    beneficial=sum(float(r['deltaPnlDiagnostic'])>0 for r in chosen_rep)
    harmful=sum(float(r['deltaPnlDiagnostic'])<0 for r in chosen_rep)
    counts={a:sum(p==a for _,p,_,_,_ in rec) for a in ['WAIT','KEEP','REPLACE']}
    return {
        'n':n,'exactAccuracy':exact/n if n else None,
        'prematureActRate':premature/n if n else None,'prematureAct':premature,
        'missedDominance':missed,'wrongDominanceSide':wrong,'predicted':counts,
        'replacePnlDiagnosticSum':pnl,'replacePnlDiagnosticMarkets':len(chosen_rep),
        'replacePnlBeneficial':beneficial,'replacePnlHarmful':harmful,
        'rows':[{'marketId':int(r['marketId']),'truth':t,'pred':p,'score':s,**parts} for r,p,t,s,parts in rec],
    }


def main()->int:
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    train=rows[:100]; val=rows[100:126]; fwd=rows[126:]
    scales={k:robust_scale([f(r.get(k)) for r in train]) for k in TRACK_KEYS+COST_KEYS}
    report={'version':'EXECUTION_MPC_V0_ORACLE','researchOnly':True,'graduationEligible':False,
            'runtimeDeployable':False,'teacherLeakageByDesign':True,
            'purpose':'Small objective-design ceiling only. Uses future KEEP-vs-REPLACE counterfactual deltas to test mature execution-control knobs before training strict-past predictors.',
            'normalization':'Train100 median absolute nonzero value per horizon component.',
            'scales':scales,'configs':[],'guardrails':['No threshold sweep against PnL.','PnL is post-hoc diagnostic only.','Same fixed 100/26/23 chronological split.','No live/Echtgeld changes.']}
    for cfg in CONFIGS:
        e={'config':cfg}
        for name,part in [('train',train),('validation',val),('forwardOos',fwd)]: e[name]=eval_rows(part,cfg,scales)
        report['configs'].append(e)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    slim=[]
    for e in report['configs']:
        slim.append({'name':e['config']['name'],**{s:{k:e[s][k] for k in ['exactAccuracy','prematureActRate','missedDominance','wrongDominanceSide','predicted','replacePnlDiagnosticSum']} for s in ['train','validation','forwardOos']}})
    print(json.dumps({'ok':True,'report':str(OUT),'scales':scales,'results':slim},ensure_ascii=False,allow_nan=True))
    return 0

if __name__=='__main__': raise SystemExit(main())
