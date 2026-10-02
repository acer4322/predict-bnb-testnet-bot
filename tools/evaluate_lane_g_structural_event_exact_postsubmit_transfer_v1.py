from __future__ import annotations
import argparse,json,math,os,sys,tempfile,zipfile,shutil
from pathlib import Path
import numpy as np,joblib
from sklearn.metrics import roc_auc_score, brier_score_loss, mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'diagnose_lane_g_exact_postsubmit_world_state_v1.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED))
    import diagnose_lane_g_exact_postsubmit_world_state_v1 as diag
    import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
    from tools import diagnose_lane_g_exact_postsubmit_world_state_v1 as diag
    from tools import train_lane_g_r264_execution_world_v1 as wm

ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']

def auc(y,p):
    return float(roc_auc_score(y,p)) if len(set(y))>1 else None

def actual_carrier_label(sim,row):
    key=str(row['key']); t0=int(row['t'])
    fills=sorted([x for x in sim.splitEvents if x.get('event')=='ROLE_FILL_SPLIT' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0], key=lambda x:int(x.get('t') or 0))
    terms=sorted([x for x in sim.slot_history if x.get('event')=='SLOT_RELEASE' and str(x.get('key'))==key and int(x.get('t') or 0)>=t0], key=lambda x:int(x.get('t') or 0))
    ft=int(fills[0]['t']) if fills else None; tt=int(terms[0]['t']) if terms else None
    if ft is None and tt is None:
        return {'eventObserved':False}
    et=min(x for x in [ft,tt] if x is not None)
    fill_first=bool(ft is not None and (tt is None or ft<=tt))
    first_qty=0.0; first_repair=0.0
    if fill_first:
        same=[x for x in fills if int(x.get('t') or 0)==ft]
        first_qty=sum(float(x.get('fillInc') or 0.0) for x in same)
        first_repair=sum(float(x.get('repairAllocated') or 0.0) for x in same)
    return {'eventObserved':True,'fillFirst':1 if fill_first else 0,'eventLagSec':max(0.0,(et-t0)/1000.0),'firstFillQty':first_qty,'firstRepairPayQty':first_repair,'fillT':ft,'terminalT':tt,'eventT':et}

def predict(bundle,row):
    x=wm.X([row],True)
    mods=bundle['models']
    pf=float(mods['fillFirst'].predict_proba(x)[0,1])
    lag=max(0.0,float(np.expm1(mods['eventLagLog'].predict(x)[0])))
    fq=max(0.0,float(mods['firstFillQty'].predict(x)[0]))
    rq=max(0.0,float(mods['firstRepairPayQty'].predict(x)[0]))
    return {'pFillFirst':pf,'predEventLagSec':lag,'predFirstFillQty':fq,'predFirstRepairPayQty':rq}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--spec',required=True); ap.add_argument('--model',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    spec_doc=json.loads(Path(a.spec).read_text(encoding='utf-8')); specs={int(k):v for k,v in spec_doc['markets'].items()}; mids=sorted(specs)
    for m,s in specs.items(): diag.ma.FROZEN[m]=s
    model=joblib.load(a.model)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_struct_transfer_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for action in ACTIONS:
                sim=diag.SnapSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:
                    r=sim.run_exact(co[m]['winner']); wr=sim.worldPostSubmit
                    lab=actual_carrier_label(sim,wr) if wr is not None else {'eventObserved':False}
                    pr=predict(model,wr) if wr is not None else None
                finally: sim.close()
                rec={'marketId':m,'action':action,'triggered':bool(r['triggered']),'triggerParityErrors':r['triggerParityErrors'],'rawCorrect':bool(r['correct']),'worldPostSubmit':wr,'actual':lab,'pred':pr}
                rows.append(rec)
                print(json.dumps({'marketId':m,'action':action,'actual':lab,'pred':pr,'rawCorrect':rec['rawCorrect']},ensure_ascii=False),flush=True)
        obs=[x for x in rows if x['actual'].get('eventObserved')]
        y=[int(x['actual']['fillFirst']) for x in obs]; p=[float(x['pred']['pFillFirst']) for x in obs]
        lag_y=[float(x['actual']['eventLagSec']) for x in obs]; lag_p=[float(x['pred']['predEventLagSec']) for x in obs]
        fobs=[x for x in obs if int(x['actual']['fillFirst'])==1]
        fq_y=[float(x['actual']['firstFillQty']) for x in fobs]; fq_p=[float(x['pred']['predFirstFillQty']) for x in fobs]
        rq_y=[float(x['actual']['firstRepairPayQty']) for x in fobs]; rq_p=[float(x['pred']['predFirstRepairPayQty']) for x in fobs]
        # Within each exact market: does higher predicted pFillFirst rank truly fill-first carriers above terminal-first carriers?
        pair_total=pair_ok=0; market_rank=[]
        for m in mids:
            rr=[x for x in obs if x['marketId']==m]
            t=o=0
            for i in range(len(rr)):
                for j in range(i+1,len(rr)):
                    yi=int(rr[i]['actual']['fillFirst']); yj=int(rr[j]['actual']['fillFirst'])
                    if yi==yj: continue
                    t+=1; pi=float(rr[i]['pred']['pFillFirst']); pj=float(rr[j]['pred']['pFillFirst'])
                    ok=(pi>pj) if yi>yj else (pj>pi)
                    o+=1 if ok else 0
            pair_total+=t; pair_ok+=o; market_rank.append({'marketId':m,'eligiblePairs':t,'correctPairs':o})
        metrics={'n':len(obs),'fillPositive':sum(y),'fillFirstAUC':auc(y,p),'fillFirstBrier':float(brier_score_loss(y,p)) if y else None,'fillFirstAccuracyAt05':float(np.mean([(pp>=.5)==bool(yy) for yy,pp in zip(y,p)])) if y else None,'eventLagMAE':float(mean_absolute_error(lag_y,lag_p)) if lag_y else None,'firstFillQtyMAE':float(mean_absolute_error(fq_y,fq_p)) if fq_y else None,'firstRepairPayQtyMAE':float(mean_absolute_error(rq_y,rq_p)) if rq_y else None,'withinMarketFillFirstPairAccuracy':(pair_ok/pair_total if pair_total else None),'withinMarketEligiblePairs':pair_total,'withinMarketCorrectPairs':pair_ok,'marketRank':market_rank}
        gates={'allTriggered':all(x['triggered'] for x in rows),'triggerParityClean':all(not x['triggerParityErrors'] for x in rows),'allCarrierEventsObserved':len(obs)==len(rows),'predictionsFinite':all(math.isfinite(float(v)) for x in obs for v in x['pred'].values()),'fillFirstAUCAboveRandom':metrics['fillFirstAUC'] is not None and metrics['fillFirstAUC']>.5,'withinMarketPairAccuracyAboveHalf':metrics['withinMarketFillFirstPairAccuracy'] is None or metrics['withinMarketFillFirstPairAccuracy']>.5}
        out={'version':'LANE_G_STRUCTURAL_EVENT_EXACT_POSTSUBMIT_TRANSFER_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'metrics':metrics,'gates':gates,'transferPass':all(gates.values()),'rawCorrectWarningCount':sum(1 for x in rows if not x['rawCorrect']),'boundary':['5 consumed H100 exact multi-action anchors only','world state sampled immediately after exact branch materialization','carrier labels use same first FILL vs SLOT_RELEASE semantics as H100 structural-event model','no terminal PnL used as training label','1825994 raw legacy warning retained separately','no fresh/no winner future/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'metrics':metrics,'gates':gates,'transferPass':out['transferPass'],'rawCorrectWarningCount':out['rawCorrectWarningCount']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
