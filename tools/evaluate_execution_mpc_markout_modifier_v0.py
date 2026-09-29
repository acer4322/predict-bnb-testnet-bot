from __future__ import annotations

import json, math, sys
from pathlib import Path
from typing import Any
import joblib
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_pair_completion_counterfactual_v3_sequence as cf
from tools.train_execution_robust_mpc_components_v1 import X, truth
from src.predict_bot import unified_controller_paper_v2 as mod

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
ART=D/'execution_robust_mpc_components_v1.joblib'
OUT=D/'execution_mpc_markout_modifier_v0_report.json'
GRID=float(mod.GRID)
EPS=1e-9

CONFIGS=[
 {'name':'BASE_SAFE','toxicSigmaAdd':0.0,'favorableSigmaAdd':0.0,'toxicEdgeShift':0.0,'favorableEdgeShift':0.0},
 {'name':'TOX_MARGIN_LIGHT','toxicSigmaAdd':0.25,'favorableSigmaAdd':0.0,'toxicEdgeShift':0.0,'favorableEdgeShift':0.0},
 {'name':'TOX_MARGIN_STRONG','toxicSigmaAdd':0.50,'favorableSigmaAdd':0.0,'toxicEdgeShift':0.0,'favorableEdgeShift':0.0},
 {'name':'TOX_ECON_LIGHT','toxicSigmaAdd':0.25,'favorableSigmaAdd':-0.10,'toxicEdgeShift':0.01,'favorableEdgeShift':-0.01},
]


def side_mid(book:dict[str,dict[float,float]], side:str)->float|None:
    bf=mod.outcome_book(book,None)
    if not bf:return None
    bid=float(bf['up_bid'] if side=='UP' else bf['down_bid'])
    ask=float(bf['up_ask'] if side=='UP' else bf['down_ask'])
    return (bid+ask)/2.0


def strict_past_markout(mid:int, checkpoint_ms:int, fill_log:list[dict[str,Any]])->dict[str,Any]:
    eligible=[e for e in fill_log if str(e.get('role'))=='MAKER' and int(e.get('eventMs') or 0)+1000<=checkpoint_ms]
    if not eligible:return {'markout1sTicks':None,'markoutFillMs':None,'markoutFillSide':None}
    f=max(eligible,key=lambda e:int(e['eventMs']))
    t0=int(f['eventMs']); side=str(f['side'])
    b=mod.PublicBookTailer(mod.BOOK_DB)
    try:
        b.advance(mid,t0); m0=side_mid(b.book,side)
        b.advance(mid,t0+1000); m1=side_mid(b.book,side)
    finally:
        b.close()
    mo=((m1-m0)/GRID) if m0 is not None and m1 is not None else None
    return {'markout1sTicks':mo,'markoutFillMs':t0,'markoutFillSide':side,'markoutAgeAtCheckpointMs':checkpoint_ms-(t0+1000)}


def preds(bundle:dict[str,Any],rows:list[dict[str,Any]])->dict[str,np.ndarray]:
    xx=X(rows)
    return {k:bundle['models'][k].predict(xx) for k in ['deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s']}


def evaluate(bundle:dict[str,Any],rows:list[dict[str,Any]],marks:list[dict[str,Any]],cfg:dict[str,Any])->dict[str,Any]:
    pp=preds(bundle,rows); out=[]
    for i,(r,mk) in enumerate(zip(rows,marks)):
        mo=mk.get('markout1sTicks')
        tox=(mo is not None and float(mo)<=-1.0)
        fav=(mo is not None and float(mo)>=1.0)
        sigma_mult=0.5 + (float(cfg['toxicSigmaAdd']) if tox else float(cfg['favorableSigmaAdd']) if fav else 0.0)
        sigma_mult=max(0.0,sigma_mult)
        rep=keep=0
        for k in pp:
            y=float(pp[k][i]); sig=float(bundle['trainResidualStd'][k]); mar=sigma_mult*sig
            if y < -mar: rep+=1
            elif y > mar: keep+=1
        f=r.get('features') or {}
        edge=f.get('lockedPairEdgePerShare'); spread=f.get('recoverySpreadTicks')
        edge_min=-0.02 + (float(cfg['toxicEdgeShift']) if tox else float(cfg['favorableEdgeShift']) if fav else 0.0)
        # Prior small probe: wider spread requires stronger evidence, represented by an extra vote requirement.
        votes_required=3 if not (spread is not None and float(spread)>1.5) else 4
        # With three tracking horizons, a 4-vote requirement means no autonomous action under wide spread.
        if rep>=votes_required and keep==0 and edge is not None and float(edge)>=edge_min: pred='REPLACE'
        elif keep>=votes_required and rep==0: pred='KEEP'
        else: pred='WAIT'
        out.append({'marketId':int(r['marketId']),'truth':truth(r),'pred':pred,'replaceVotes':rep,'keepVotes':keep,'edge':edge,'edgeMin':edge_min,'spreadTicks':spread,**mk})
    tr=[x['truth'] for x in out]; pr=[x['pred'] for x in out]; n=len(out)
    premature=sum(t=='WAIT' and p!='WAIT' for t,p in zip(tr,pr)); missed=sum(t!='WAIT' and p=='WAIT' for t,p in zip(tr,pr)); wrong=sum(t in {'KEEP','REPLACE'} and p in {'KEEP','REPLACE'} and t!=p for t,p in zip(tr,pr))
    reps=[r for r,p in zip(rows,pr) if p=='REPLACE' and r.get('deltaPnlDiagnostic') is not None]
    return {'n':n,'exactAccuracy':sum(t==p for t,p in zip(tr,pr))/n if n else None,'prematureActRate':premature/n if n else None,'prematureAct':premature,'missedDominance':missed,'wrongDominanceSide':wrong,'predicted':{a:pr.count(a) for a in ['WAIT','KEEP','REPLACE']},'replacePnlDiagnosticSum':sum(float(r['deltaPnlDiagnostic']) for r in reps),'replacePnlDiagnosticMarkets':len(reps),'markoutCoverage':sum(x.get('markout1sTicks') is not None for x in out)/n if n else None,'toxicCount':sum(x.get('markout1sTicks') is not None and float(x['markout1sTicks'])<=-1 for x in out),'favorableCount':sum(x.get('markout1sTicks') is not None and float(x['markout1sTicks'])>=1 for x in out),'rows':out}


def main()->int:
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    parts={'validation':rows[100:126],'forwardOos':rows[126:]}
    bundle=joblib.load(ART)
    marks_by_mid={}
    for part in parts.values():
        for r in part:
            mid=int(r['marketId'])
            if mid in marks_by_mid:continue
            rr=cf.run_recovery(mid,False,0)
            marks_by_mid[mid]=strict_past_markout(mid,int(r['checkpointMs']),list(rr.get('fillLog') or []))
            print(json.dumps({'marketId':mid,**marks_by_mid[mid]},ensure_ascii=False),flush=True)
    report={'version':'EXECUTION_MPC_MARKOUT_MODIFIER_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'semantics':'Use only a last Maker fill whose +1s markout is already observable at checkpoint. Markout modifies action uncertainty/economic margin; it never directly cancels or selects a side. Winner/PnL are not runtime inputs.','configs':[],'guardrails':['No threshold sweep against PnL.','Fixed small config menu chosen before results.','Public-book source-time replay is development-grade only; not formal fresh receipt-frontier evidence.','PnL diagnostic post-hoc only.']}
    for cfg in CONFIGS:
        e={'config':cfg}
        for name,part in parts.items(): e[name]=evaluate(bundle,part,[marks_by_mid[int(r['marketId'])] for r in part],cfg)
        report['configs'].append(e)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    slim=[]
    for e in report['configs']:
        slim.append({'name':e['config']['name'],**{p:{k:e[p][k] for k in ['exactAccuracy','prematureActRate','missedDominance','wrongDominanceSide','predicted','markoutCoverage','toxicCount','favorableCount','replacePnlDiagnosticSum']} for p in parts}})
    print(json.dumps({'ok':True,'report':str(OUT),'configs':slim},ensure_ascii=False,allow_nan=True))
    return 0
if __name__=='__main__': raise SystemExit(main())
