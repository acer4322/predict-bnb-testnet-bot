from __future__ import annotations
import json,lzma,sys
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_management_provenance_bridge_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';EPS=1e-9
PH=P/'r4_p0b_phase_routed_shadow_controller_v1.json';OWN=P/'r4_management_testbed_ownership_continuity_v1_integrated_shadow_v2.json';OUT=P/'r4_management_wholemarket_marginal_role_context_v1.json'

def teacher(r):
    x=str(r.get('future_management_label_5s') or '')
    return 'CONTINUE' if x=='CONTINUE_WEAK' else 'HANDOFF' if x=='HANDOFF_ALLOW' else 'OBSERVE' if x=='OBSERVE_NO_EVENT' else 'UNKNOWN'

def prefix_before_submit(j,mr):
    rid=str(mr.get('submittedResponsibilityId') or '');t=int(mr['t'])
    for i,e in enumerate(j):
        if e.get('event_type')=='RESPONSIBILITY_OPENED' and str(e.get('responsibility_id'))==rid and int(e.get('received_at_ms') or 0)==t:return j[:i]
    return [e for e in j if int(e.get('received_at_ms') or 0)<t]

def agg(oj,side,cut):
    st=led.replay_objective_journal([e for e in oj if int(e.get('received_at_ms') or 0)<=int(cut)])
    active={k:v for k,v in st.items() if float(v.get('residual_objective_deficit_qty') or 0)>EPS or float(v.get('reserved_same_objective_commitment_qty') or 0)>EPS}
    ss=[v for v in active.values() if str(v.get('side'))==side];os=[v for v in active.values() if str(v.get('side')) in {'UP','DOWN'} and str(v.get('side'))!=side]
    return {'same_active':float(len(ss)),'opp_active':float(len(os)),'same_residual':float(sum(float(v.get('residual_objective_deficit_qty') or 0) for v in ss)),'opp_residual':float(sum(float(v.get('residual_objective_deficit_qty') or 0) for v in os)),'same_reserved':float(sum(float(v.get('reserved_same_objective_commitment_qty') or 0) for v in ss)),'opp_reserved':float(sum(float(v.get('reserved_same_objective_commitment_qty') or 0) for v in os)),'same_confirmed':float(sum(float(v.get('confirmed_same_objective_completion_qty') or 0) for v in ss)),'opp_confirmed':float(sum(float(v.get('confirmed_same_objective_completion_qty') or 0) for v in os))}

def mean(rows,k):
    z=[float(r[k]) for r in rows if r.get(k) is not None and np.isfinite(float(r[k]))];return float(np.mean(z)) if z else None

def main():
    ph=json.loads(PH.read_text(encoding='utf-8'));targets=[r for r in ph['trace'] if r.get('phase')=='MANAGEMENT_60_180' and int(r.get('build_now') or 0)==1];bykey={(int(r['marketId']),int(r['t']),str(r.get('side')),str(r.get('kind'))):r for r in targets}
    own=json.loads(OWN.read_text(encoding='utf-8'));ownmap={(int(r['marketId']),int(r['t'])):r for r in own.get('rows',[])}
    out=[];missing=[]
    for mid in sorted(set(int(r['marketId']) for r in targets)):
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));res=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);j=res.get('provenanceJournal') or []
        for mr in res.get('managementShadowRows') or []:
            key=(mid,int(mr['t']),str(mr.get('side')),str(mr.get('kind')));pr=bykey.get(key)
            if pr is None:continue
            pref=prefix_before_submit(j,mr);oj,_,_=led.materialize(mid,pref);side=str(mr.get('side'));t=int(mr['t']);cur=agg(oj,side,t);s5=agg(oj,side,t-5000);s15=agg(oj,side,t-15000);q=max(EPS,abs(float(mr.get('requested_qty') or 0)));gap=max(EPS,abs(float(mr.get('abs_gap') or 0)));ow=ownmap.get((mid,t),{})
            orig=str(pr.get('shadow_decision') or '');tr=teacher(pr);err='MATCH' if orig==tr else f'{orig}_VS_{tr}'
            z={'marketId':mid,'t':t,'side':side,'kind':str(mr.get('kind')),'originalDecision':orig,'teacherDecision':tr,'errorType':err,'requested_qty':q,'abs_gap':gap,'candidate_to_gap':q/gap,'same_active':cur['same_active'],'opp_active':cur['opp_active'],'same_residual_units':cur['same_residual']/q,'opp_residual_units':cur['opp_residual']/q,'same_reserved_units':cur['same_reserved']/q,'same_confirmed_units':cur['same_confirmed']/q,'same_credit_capacity_ratio':min(1.,cur['same_residual']/q),'residual_excess_units':(cur['same_residual']-q)/q,'same_active_delta5':cur['same_active']-s5['same_active'],'same_active_delta15':cur['same_active']-s15['same_active'],'opp_active_delta5':cur['opp_active']-s5['opp_active'],'opp_active_delta15':cur['opp_active']-s15['opp_active'],'same_residual_delta5_units':(cur['same_residual']-s5['same_residual'])/q,'same_residual_delta15_units':(cur['same_residual']-s15['same_residual'])/q,'opp_residual_delta5_units':(cur['opp_residual']-s5['opp_residual'])/q,'opp_residual_delta15_units':(cur['opp_residual']-s15['opp_residual'])/q,'pExistingWeak5s':ow.get('pExistingWeak5s'),'pExistingDom5s':ow.get('pExistingDom5s'),'futureEconomicProgress5s':int(bool(int(pr.get('floorImproved5s') or 0) or int(pr.get('absNetReduced5s') or 0))),'futureWeakMakerFill5s':int(pr.get('futureWeakMakerFill5s') or 0)}
            out.append(z)
    got={(r['marketId'],r['t']) for r in out}
    for r in targets:
        if (int(r['marketId']),int(r['t'])) not in got:missing.append({'marketId':r['marketId'],'t':r['t']})
    keys=['candidate_to_gap','same_active','same_residual_units','same_credit_capacity_ratio','residual_excess_units','same_active_delta5','same_active_delta15','opp_active_delta5','opp_active_delta15','same_residual_delta5_units','same_residual_delta15_units','pExistingWeak5s']
    anatomy={}
    for et,g in __import__('itertools').groupby(sorted(out,key=lambda r:r['errorType']),key=lambda r:r['errorType']):
        rr=list(g);anatomy[et]={'n':len(rr),**{k:mean(rr,k) for k in keys},'futureEconomicProgressRate':mean(rr,'futureEconomicProgress5s')}
    # Focus on the historical largest management error: CONTINUE_VS_OBSERVE vs correct CONTINUE and MATCH overall.
    cvo=[r for r in out if r['errorType']=='CONTINUE_VS_OBSERVE'];cc=[r for r in out if r['originalDecision']=='CONTINUE' and r['teacherDecision']=='CONTINUE'];
    report={'version':'R4_MANAGEMENT_WHOLEMARKET_MARGINAL_ROLE_CONTEXT_V1','researchOnly':True,'actionAuthority':False,'cohortStatus':'CONSUMED_WHOLEMARKET20_INTEGRATION_DIAGNOSTIC_ONLY','markets':len(set(r['marketId'] for r in out)),'rowsExpected':len(targets),'rowsMatched':len(out),'missing':missing,'errorCounts':dict(Counter(r['errorType'] for r in out)),'errorAnatomy':anatomy,'focus':{'continueVsObserveN':len(cvo),'correctContinueN':len(cc),'continueVsObserveMeanSameResidualUnits':mean(cvo,'same_residual_units'),'correctContinueMeanSameResidualUnits':mean(cc,'same_residual_units'),'continueVsObserveMeanSameActiveDelta15':mean(cvo,'same_active_delta15'),'correctContinueMeanSameActiveDelta15':mean(cc,'same_active_delta15'),'continueVsObserveMeanPExistingWeak5s':mean(cvo,'pExistingWeak5s'),'correctContinueMeanPExistingWeak5s':mean(cc,'pExistingWeak5s')},'rows':out,'interpretationBoundary':'Strict-past candidate-side Objective Ledger context plus retained Ownership Continuity, inserted into the consumed whole-market testbed as annotations only. No policy mutation or promotion.'}
    OUT.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({k:report[k] for k in ['markets','rowsExpected','rowsMatched','errorCounts','focus']},indent=2))
if __name__=='__main__':main()
