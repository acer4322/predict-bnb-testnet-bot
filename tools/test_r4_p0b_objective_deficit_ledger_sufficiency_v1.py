from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OUT=P/'r4_p0b_objective_deficit_ledger_sufficiency_v1.json'

ROLE_CREDIT={
    'PREPOSITION_REPAIR_SUBSTITUTE': True,
    'PARALLEL_STATE_SHAPING': False,
}

def load_rows():
    rows=[]
    for p in sorted(P.glob('r4_p0b_role_anatomy_extension_branches_*_v1.json')):
        d=json.loads(p.read_text(encoding='utf-8'))
        rows.extend(d.get('rows') or [])
    return rows

def main():
    rows=load_rows()
    realized=[r for r in rows if r.get('branchValid') and r.get('role') not in {'EXECUTION_NO_REALIZATION','INVALID_BRANCH_DIVERGENCE'}]
    grouping=[r for r in realized if r.get('role') in ROLE_CREDIT]
    details=[]
    root_unique_ok=0; side_fungible_ok=0
    for r in grouping:
        expected_credit=ROLE_CREDIT[r['role']]
        root_unique_credit=False
        side_fungible_credit=True
        ru=(root_unique_credit==expected_credit)
        sf=(side_fungible_credit==expected_credit)
        root_unique_ok+=int(ru); side_fungible_ok+=int(sf)
        c=r.get('candidate') or {}
        details.append({
            'marketId':r.get('marketId'), 'candidateKey':r.get('candidateKey'), 'role':r.get('role'),
            'candidateSide':c.get('candidateSide'), 'parentLogical':c.get('parentLogical'),
            'expectedCrossRootCredit':expected_credit,
            'ROOT_UNIQUE_correct':ru, 'SIDE_FUNGIBLE_correct':sf,
        })
    counts=Counter(r.get('role') for r in rows)
    realized_counts=Counter(r.get('role') for r in realized)
    n=len(grouping)
    objective_required=(realized_counts.get('PREPOSITION_REPAIR_SUBSTITUTE',0)>0 and realized_counts.get('PARALLEL_STATE_SHAPING',0)>0)
    # Current candidate/provenance-origin fields are audited conservatively: candidate rows expose
    # responsibility/logical identity and geometry, but no objective identity or deficit-origin key.
    sample_keys=set()
    for r in rows:
        sample_keys.update((r.get('candidate') or {}).keys())
    objective_fields=[k for k in sorted(sample_keys) if 'objective' in k.lower() or 'deficit_origin' in k.lower() or 'credit_scope' in k.lower()]
    art={
        'version':'R4_P0B_OBJECTIVE_DEFICIT_LEDGER_SUFFICIENCY_V1',
        'status':'KEEP_OBJECTIVE_LAYER_REQUIRED' if objective_required else 'INCONCLUSIVE_SUPPORT',
        'researchOnly':True,
        'actionAuthority':False,
        'extension':{
            'candidates':len(rows),'branchValid':sum(bool(r.get('branchValid')) for r in rows),
            'roleCounts':dict(counts),'realizedRoleCounts':dict(realized_counts)
        },
        'groupingAudit':{
            'eligiblePrepositionOrStateShaping':n,
            'ROOT_UNIQUE':{'correct':root_unique_ok,'accuracy':root_unique_ok/n if n else None,
                           'semantic':'never cross-root credit'},
            'SIDE_FUNGIBLE':{'correct':side_fungible_ok,'accuracy':side_fungible_ok/n if n else None,
                             'semantic':'always cross-root credit for same side'},
            'oracleDiagnosticAccuracy':1.0 if n else None,
            'interpretation':'Both PREPOSITION and STATE_SHAPING occur. Therefore objective membership cannot be inferred from root inequality or side equality alone.' if objective_required else 'Insufficient role support.'
        },
        'currentRepresentationAudit':{
            'objectiveIdentityFieldsPresentAtCandidate':objective_fields,
            'explicitPortfolioObjectiveIdPresent': 'portfolio_objective_id' in sample_keys,
            'explicitDeficitOriginIdPresent': any('deficit_origin' in k.lower() for k in sample_keys),
            'explicitCreditScopePresent': any('credit_scope' in k.lower() for k in sample_keys),
            'finding':'Current strict-past candidate representation lacks immutable objective/deficit-origin identity. Role outcome must not be used to synthesize runtime objective identity.'
        },
        'requiredLedgerV1':{
            'identity':['portfolio_objective_id','objective_origin_event_id','objective_type_at_open'],
            'quantities':['gross_objective_deficit_qty','confirmed_same_objective_completion_qty','reserved_same_objective_commitment_qty','credited_same_objective_completion_qty','residual_objective_deficit_qty'],
            'membership':['responsibility_ids','credit_scope','parent_objective_id','predecessor_objective_id'],
            'events':['OBJECTIVE_OPENED','RESPONSIBILITY_ATTACHED','OBJECTIVE_CREDIT_GRANTED','OBJECTIVE_CREDIT_APPLIED','RESPONSIBILITY_DETACHED','OBJECTIVE_COMPLETED','OBJECTIVE_TERMINATED']
        },
        'details':details,
        'guards':['No runtime objective id inferred from future role/outcome','No R3/8781/Echtgeld changes','2026-08-16 SEALED remains excluded']
    }
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'extension':art['extension'],'groupingAudit':art['groupingAudit'],'representation':art['currentRepresentationAudit']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
