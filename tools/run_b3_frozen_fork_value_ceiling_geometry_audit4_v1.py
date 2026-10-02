from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path

BTOL=1e-8
CTOL=1e-7
CLAIM_CHECKS=(
    'fills90','alternations90','completion90','R0OutstandingNotWorse',
    'terminalLiabilityNotIncreased','buyNotionalDropExplained'
)
EXPECTED=[
    ('D1',1823598,31,1788159012971),
    ('D2',1823603,102,1788159329536),
    ('R1',1823614,78,1788159922047),
    ('R2',1823755,27,1788160211421),
]

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def close(a,b,tol=BTOL):
    return math.isclose(float(a),float(b),rel_tol=0.0,abs_tol=tol)

def arm_closure(arm):
    cf=arm['cashflowAttribution']; pre=cf['prefix']; term=arm['terminalEconomics']
    du=sum(float(v.get('Udelta',0.0)) for v in cf['categories'].values())
    dd=sum(float(v.get('Ddelta',0.0)) for v in cf['categories'].values())
    ru=float(pre['U'])+du-float(term['U'])
    rd=float(pre['D'])+dd-float(term['D'])
    return {'U':ru,'D':rd,'pass':abs(ru)<=CTOL and abs(rd)<=CTOL}

def geom(du,dd,delta):
    out={'deltaU':du,'deltaD':dd,'delta':delta}
    if abs(du)<=BTOL and abs(dd)<=BTOL:
        out.update({'class':'ZERO_VECTOR','pStar':None,'pStarStatus':'ALL_P_TIE','pMaterialUpper':None,'aMaterialLower':None,'rawMaxInformationPremium':0.0})
        return out
    if du<0 and dd>0:
        s=dd-du; p=dd/s
        p_lo=(dd-delta)/s; p_hi=(dd+delta)/s
        raw_max=dd*(-du)/s
        out.update({'class':'TRADEOFF_P_DOWN_A_UP','pStar':p,'pStarStatus':'FINITE_INTERNAL',
                    'pMaterialUpper':p_lo,'aMaterialLower':p_hi,'rawMaxInformationPremium':raw_max})
        return out
    if du>0 and dd>0: cls='P_BILATERAL_POSITIVE'
    elif du<0 and dd<0: cls='A_BILATERAL_POSITIVE'
    else: cls='OTHER_OR_DEGENERATE'
    out.update({'class':cls,'pStar':None,'pStarStatus':'NO_INTERNAL_CROSSING','pMaterialUpper':None,'aMaterialLower':None,'rawMaxInformationPremium':None})
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True)
    ap.add_argument('--prereg',required=True)
    ap.add_argument('--output',required=True)
    a=ap.parse_args()
    ip=Path(a.input); pp=Path(a.prereg); op=Path(a.output)
    src=json.loads(ip.read_text(encoding='utf-8')); pre=json.loads(pp.read_text(encoding='utf-8'))
    actual_hash=sha256(ip); expected_hash=str(pre['input']['compactSha256']).lower()
    hard=[]
    hard.append(('inputHash',actual_hash.lower()==expected_hash))
    hard.append(('sourceVerdict',src.get('verdict')=='SCOPED_B3_VALUE_ORDER_NON_TOTAL'))
    hard.append(('sourceCorrectness',bool(src.get('allCorrectnessPass'))))
    hard.append(('sourceExercise',bool(src.get('allExercisePass'))))
    rows=src.get('rows') or []
    hard.append(('rowCount',len(rows)==4))
    seen=set(); outrows=[]
    raw_sum=0.0; claim_sum=0.0; raw_material=0; claim_material=0
    raw_D=False;raw_R=False;claim_D=False;claim_R=False
    max_cash_res=0.0
    for idx,r in enumerate(rows):
        exp=EXPECTED[idx] if idx<len(EXPECTED) else None
        key=(int(r['marketId']),int(r['phaseOrdinal']),int(r['eventTimestampMs']))
        dedup=key not in seen;seen.add(key)
        cohort_ok=bool(exp and r['panel']==exp[0] and key==(exp[1],exp[2],exp[3]))
        controls=r['controls']
        control_ok=all(bool(controls.get(k)) for k in (
            'allArmsCorrect','commonForkPrefixExact','N_equals_AFullBehaviorLedgerTerminalParity',
            'P_equals_P_REPEATFullBehaviorLedgerTerminalParity','allOneShotExactlyOnce','allSelectedPhysicalSubmit'))
        armA=r['arms']['A']; armP=r['arms']['P']
        A=armA['terminalEconomics']; P=armP['terminalEconomics']; eo=r['economicOrdering']
        du=float(P['U'])-float(A['U']);dd=float(P['D'])-float(A['D'])
        dm=float(P['M'])-float(A['M']);dt=float(P['T'])-float(A['T'])
        df=float(P['Floor'])-float(A['Floor']);db=float(P['Best'])-float(A['Best'])
        endpoint_ok=all((close(du,eo['deltaU']),close(dd,eo['deltaD']),close(dm,eo['deltaM']),close(dt,eo['deltaT']),close(df,eo['deltaFloor']),close(db,eo['deltaBest'])))
        ca=arm_closure(armA);cp=arm_closure(armP)
        max_cash_res=max(max_cash_res,abs(ca['U']),abs(ca['D']),abs(cp['U']),abs(cp['D']))
        checks=r['claimGates']['P_over_A']['checks']
        c=all(bool(checks.get(k,False)) for k in CLAIM_CHECKS)
        reported_pass=bool(r['claimGates']['P_over_A'].get('pass'))
        claim_predicate_consistent=(c==reported_pass)
        delta=float(eo.get('materialityDelta',0.01))
        g=geom(du,dd,delta)
        rawU=max(0.0,du);rawD=max(0.0,dd);rawOuter=max(rawU,rawD)
        claimU=rawU if c else 0.0;claimD=rawD if c else 0.0;claimOuter=max(claimU,claimD)
        raw_sum+=rawOuter;claim_sum+=claimOuter
        if rawOuter>delta+BTOL:
            raw_material+=1; raw_D=raw_D or str(r['panel']).startswith('D');raw_R=raw_R or str(r['panel']).startswith('R')
        if claimOuter>delta+BTOL:
            claim_material+=1; claim_D=claim_D or str(r['panel']).startswith('D');claim_R=claim_R or str(r['panel']).startswith('R')
        claim_info_max=(g['rawMaxInformationPremium'] if c and g['rawMaxInformationPremium'] is not None else 0.0)
        if abs(du)<=BTOL and abs(dd)<=BTOL: claim_info_max=0.0
        fee_boundary=(armA['selectedExecution'].get('feeTelemetry'),armP['selectedExecution'].get('feeTelemetry'))
        row_ok=bool(r.get('correctnessPass') and r.get('exercisePass') and dedup and cohort_ok and control_ok and endpoint_ok and ca['pass'] and cp['pass'] and claim_predicate_consistent and claimOuter<=rawOuter+BTOL)
        outrows.append({
            'panel':r['panel'],'marketId':int(r['marketId']),'phaseOrdinal':int(r['phaseOrdinal']),'eventTimestampMs':int(r['eventTimestampMs']),
            'correctnessPass':row_ok,'dedupUnique':dedup,'cohortExact':cohort_ok,'controlsPass':control_ok,'endpointDeltaReconstructionPass':endpoint_ok,
            'cashflowClosure':{'A':ca,'P':cp},'claimChecks':{k:bool(checks.get(k,False)) for k in CLAIM_CHECKS},'claimAdmissibleP':c,
            'geometry':g,
            'rawCeiling':{'UP':rawU,'DOWN':rawD,'outer':rawOuter,'material':rawOuter>delta+BTOL,'oracleUP':'P' if du>BTOL else 'A','oracleDOWN':'P' if dd>BTOL else 'A'},
            'claimCeiling':{'UP':claimU,'DOWN':claimD,'outer':claimOuter,'material':claimOuter>delta+BTOL,'maxGeometricInformationPremium':claim_info_max},
            'feeTelemetry':{'A':fee_boundary[0],'P':fee_boundary[1]},
            'r0FullyServed':all(abs(float(v))<=BTOL for v in armA['R0Service']['terminalOutstandingByR0'].values()) and all(abs(float(v))<=BTOL for v in armP['R0Service']['terminalOutstandingByR0'].values()),
            'terminalOutstandingTotal':{'A':float(armA['suffixActivityRisk']['terminalOutstandingTotal']),'P':float(armP['suffixActivityRisk']['terminalOutstandingTotal'])}
        })
        hard.append((f'row{idx+1}',row_ok))
    correctness=all(v for _,v in hard)
    epsilon=float(pre['materiality']['aggregateEpsilon'])
    each_claim_small=all(x['claimCeiling']['outer']<=float(x['geometry']['delta'])+BTOL for x in outrows)
    agg_claim_small=claim_sum<=epsilon+BTOL
    claim_zero=abs(claim_sum)<=BTOL and all(abs(x['claimCeiling']['outer'])<=BTOL for x in outrows)
    raw_scarcity=(raw_sum<=epsilon+BTOL and all(x['rawCeiling']['outer']<=float(x['geometry']['delta'])+BTOL for x in outrows))
    cost_unresolved=any('NO_SEPARATE_FEE_COMPONENT' in str(x['feeTelemetry']['A']) or 'NO_SEPARATE_FEE_COMPONENT' in str(x['feeTelemetry']['P']) for x in outrows)
    if not correctness:
        verdict='CORRECTNESS_STOP';tags=[];disposition='STOP'
    elif each_claim_small and agg_claim_small:
        verdict='VALUE_CEILING_TOO_SMALL_FOR_THIS_FROZEN_CLAIM_CLASS'
        tags=['CLAIM_ADMISSIBILITY_BINDING'] if not raw_scarcity else ['RAW_VALUE_SCARCITY']
        if claim_zero: tags.append('FROZEN_MODEL_CLAIM_CLASS_CEILING_ZERO')
        disposition='DO_NOT_START_BELIEF_PROGRAM_FOR_THIS_FROZEN_CLAIM_CLASS'
    else:
        verdict='OPPORTUNITY_UPPER_BOUND_ONLY' if cost_unresolved else 'INSUFFICIENT_REPLICATED_OPPORTUNITY_BUDGET'
        tags=[];disposition='NO_AUTOMATIC_BELIEF_PROGRAM'
    if cost_unresolved: tags.append('NET_COST_SCOPE_UNRESOLVED');tags.append('REAL_WORLD_NET_EDGE_NOT_IDENTIFIED')
    tags.append('NO_ALPHA_PROMOTION')
    result={
        'version':'B3_FROZEN_FORK_VALUE_CEILING_GEOMETRY_AUDIT4_V1_20260908','researchOnly':True,'runtimeAuthority':False,
        'sourceVerdictPreserved':'SCOPED_B3_VALUE_ORDER_NON_TOTAL','correctnessPass':correctness,'hardChecks':dict(hard),
        'summary':{
            'markets':len(outrows),'economicPairs':len(outrows),'newHftBranches':0,'newMarkets':0,'workerJobs':0,'training':0,
            'rawOuterCeilingSum':raw_sum,'claimOuterCeilingSum':claim_sum,'aggregateMaterialityEpsilon':epsilon,
            'rawMaterialMarkets':raw_material,'claimMaterialMarkets':claim_material,
            'rawPanelCoverage':{'D':raw_D,'R':raw_R},'claimPanelCoverage':{'D':claim_D,'R':claim_R},
            'eachClaimOuterLeDelta':each_claim_small,'aggregateClaimOuterLeEpsilon':agg_claim_small,'claimCeilingExactZero':claim_zero,
            'rawValueScarcity':raw_scarcity,'maxCashflowClosureResidual':max_cash_res,
            'claimActionSetInterpretation':'A only in any P-inadmissible market; A/P allowed only where all six frozen non-economic P_over_A checks pass',
            'claimInformationPremiumStatus':'ZERO_FOR_ALL_P_IN_THIS_FIXED_FOUR_CLASS' if claim_zero else 'NOT_FULLY_IDENTIFIED',
            'resolvedWinnerOracleStatus':'NOT_COMPUTED_NOT_REQUIRED_FOR_ZERO_CLAIM_CEILING_CERTIFICATE',
            'costScope':'NET_COST_SCOPE_UNRESOLVED' if cost_unresolved else 'FROZEN_COST_SCOPE_ONLY'
        },
        'rows':outrows,'verdict':verdict,'tags':tags,'beliefProgramDisposition':disposition,
        'b5Status':'B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE','alphaStatus':'NO_ALPHA_PROMOTION',
        'boundaries':[
            'existing four preregistered one-shot A/P forks only','winner-free outer geometry; settlementPnlPosthoc not used for cohort/gates/verdict',
            'claim admissibility uses only six frozen non-economic P_over_A checks','raw payoff opportunity is preserved even when P is claim-inadmissible',
            'claim-zero is local to fixed four one-shot pi_N class and existing claim contract','real-world net fee/cost edge unresolved',
            'no HFT/no worker/no fresh/no training/no runtime rule/no alpha promotion'
        ],
        'sha256':{'inputCompact':actual_hash,'prereg':sha256(pp)}
    }
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'correctnessPass':correctness,'verdict':verdict,'tags':tags,'summary':result['summary']},ensure_ascii=False))

if __name__=='__main__':main()
