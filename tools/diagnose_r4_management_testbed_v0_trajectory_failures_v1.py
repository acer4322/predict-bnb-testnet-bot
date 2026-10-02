from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PH=P/'r4_p0b_phase_routed_shadow_controller_v1.json'
OUT=P/'r4_management_testbed_v0_trajectory_failures_v1.json'

def safe(x,default=0.0):
    try:return float(x)
    except:return default

def main():
    d=json.loads(PH.read_text(encoding='utf-8'))
    rows=[r for r in d.get('trace',[]) if r.get('phase')=='MANAGEMENT_60_180' and int(r.get('build_now') or 0)==1]
    cats={}
    details=[]
    for r in rows:
        dec=str(r.get('shadow_decision') or '')
        fut=str(r.get('future_management_label_5s') or '')
        fill=safe(r.get('futureWeakMakerFill5s'))>0
        fi=safe(r.get('floorImproved5s'))>0
        ai=safe(r.get('absNetReduced5s'))>0
        if not fill:
            outcome='NO_EXECUTION_PROGRESS'
        elif fi and ai:
            outcome='EXECUTION_ECON_PROGRESS'
        elif fi or ai:
            outcome='EXECUTION_PARTIAL_ECON_PROGRESS'
        else:
            outcome='EXECUTION_NO_ECON_PROGRESS'
        if fut=='CONTINUE_WEAK': teacher='CONTINUE'
        elif fut=='HANDOFF_ALLOW': teacher='HANDOFF'
        elif fut=='OBSERVE_NO_EVENT': teacher='OBSERVE'
        else: teacher='UNKNOWN'
        if teacher=='UNKNOWN': err='UNLABELED'
        elif dec==teacher: err='MATCH'
        else: err=f'{dec}_VS_{teacher}'
        key=f'{err}|{outcome}'
        cats[key]=cats.get(key,0)+1
        details.append({k:r.get(k) for k in ['marketId','t','seconds_left','side','kind','floor','absNet','shadow_decision','reason','future_management_label_5s','futureWeakMakerFill5s','floorImproved5s','absNetReduced5s','p_m0_continue','p_transition','p_m1_continue','p_m1_handoff','p_m1_observe'] }|{'teacherDecision':teacher,'outcomeClass':outcome,'errorClass':err})
    # aggregate by decision/teacher/outcome
    def count(field):
        z={}
        for x in details:z[str(x.get(field))]=z.get(str(x.get(field)),0)+1
        return z
    # highest-risk misses: manager says CONTINUE when teacher says OBSERVE/HANDOFF and no progress; manager OBSERVE when teacher CONTINUE and progress existed
    high=[]
    for x in details:
        risky=(x['shadow_decision']=='CONTINUE' and x['teacherDecision']!='CONTINUE' and x['outcomeClass'] in ('NO_EXECUTION_PROGRESS','EXECUTION_NO_ECON_PROGRESS')) or (x['shadow_decision']=='OBSERVE' and x['teacherDecision']=='CONTINUE') or (x['shadow_decision']=='HANDOFF' and x['teacherDecision']=='CONTINUE')
        if risky: high.append(x)
    out={
      'version':'R4_MANAGEMENT_TESTBED_V0_TRAJECTORY_FAILURES_V1','researchOnly':True,'actionAuthority':False,
      'rows':len(rows),'markets':len(set(int(x['marketId']) for x in details)),
      'decisionCounts':count('shadow_decision'),'teacherCounts':count('teacherDecision'),'outcomeCounts':count('outcomeClass'),'errorCounts':count('errorClass'),
      'jointFailureCounts':dict(sorted(cats.items(), key=lambda kv:(-kv[1],kv[0]))),
      'highRiskDivergences':high,
      'highRiskDivergenceCount':len(high)
    }
    OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:out[k] for k in ['rows','markets','decisionCounts','teacherCounts','outcomeCounts','errorCounts','jointFailureCounts','highRiskDivergenceCount']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
