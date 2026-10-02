from __future__ import annotations
import glob,json,math
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
EXP=P/'r4_p0b_stage3_expanded48_dataset_v2.csv'
OUT=P/'r4_repair_carrier_delay_teacher_v1.csv'
REP=P/'r4_repair_carrier_delay_teacher_v1.json'

def extract_row(r):
    key=str(r.get('candidateKey') or '')
    t=int(r.get('candidateT') or r.get('t') or 0)
    maps=[]
    ca=r.get('creditAudit') if isinstance(r.get('creditAudit'),dict) else {}
    maps.extend(ca.get('mappings') or [])
    maps.extend(r.get('creditMappings') or [])
    needs=[];fills=[];kinds=[];lifes=[]
    for m in maps:
        try:n=int(m.get('targetNeedMs') or 0)
        except:n=0
        if t>0 and n>=t:needs.append((n-t)/1000.0)
        try:f=int(m.get('targetAdditiveFirstFillMs') or 0)
        except:f=0
        if t>0 and f>=t:fills.append((f-t)/1000.0)
        kinds.extend([str(x) for x in (m.get('additiveFutureCarrierKindsAfterCredit') or [])])
        if m.get('targetLifecycleClass'):lifes.append(str(m['targetLifecycleClass']))
    return {'candidateKey':key,'auditRole':str(r.get('knownRole') or ''),'auditCandidateT':t,
            'futureCarrierNeedDelayS':min(needs) if needs else math.nan,
            'futureCarrierFillDelayS':min(fills) if fills else math.nan,
            'futureCarrierMappingCount':len(maps),'futureCarrierKindMain':int('MAIN' in kinds),'futureCarrierKindOption':int('OPTION' in kinds),
            'futureCarrierLifecycleFutureRoot':int(any('FUTURE_ROOT' in x for x in lifes))}

def main():
    teacher={}
    j=json.loads((P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json').read_text())
    for r in j.get('rows',[]):
        z=extract_row(r)
        if z['candidateKey']:teacher[z['candidateKey']]=z
    for fp in glob.glob(str(P/'r4_p0b_objective_counterfactual_audit_ambiguous_chunk_*_v1.json')):
        j=json.loads(Path(fp).read_text())
        for r in j.get('rows',[]):
            z=extract_row(r)
            if z['candidateKey'] and z['candidateKey'] not in teacher:teacher[z['candidateKey']]=z
    d=pd.read_csv(EXP)
    td=pd.DataFrame(list(teacher.values()))
    m=d.merge(td,on='candidateKey',how='left')
    m['labelNeedWithin15s']=(m.futureCarrierNeedDelayS<=15).astype('Int64').where(m.futureCarrierNeedDelayS.notna())
    m['labelNeedWithin30s']=(m.futureCarrierNeedDelayS<=30).astype('Int64').where(m.futureCarrierNeedDelayS.notna())
    keep=m[m.futureCarrierNeedDelayS.notna()].copy()
    keep.to_csv(OUT,index=False)
    byrole={}
    for role,g in keep.groupby('knownRole'):
        byrole[str(role)]={'n':int(len(g)),'medianNeedDelayS':float(g.futureCarrierNeedDelayS.median()),'pNeed15':float((g.futureCarrierNeedDelayS<=15).mean()),'pNeed30':float((g.futureCarrierNeedDelayS<=30).mean())}
    rep={'version':'R4_REPAIR_CARRIER_DELAY_TEACHER_V1','researchOnly':True,'labelOnlyFutureFields':['futureCarrierNeedDelayS','futureCarrierFillDelayS','labelNeedWithin15s','labelNeedWithin30s'],'runtimeLeakageForbidden':True,'expandedRows':int(len(d)),'auditTeacherKeys':int(len(teacher)),'matchedRows':int(m.futureCarrierMappingCount.notna().sum()),'needDelayRows':int(len(keep)),'markets':int(keep.marketId.nunique()),'byRole':byrole,'outputCsv':str(OUT.relative_to(ROOT)).replace('\\','/')}
    REP.write_text(json.dumps(rep,indent=2,ensure_ascii=False));print(json.dumps(rep,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
