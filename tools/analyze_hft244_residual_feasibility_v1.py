"""Zero-engine conditional topology audit on saved route witness and frozen AST."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace, MethodType

ROOT=Path(__file__).resolve().parents[1]
FROZEN=ROOT/'.lan_worker_v1/root_family_support_stagea12_20260910_v1'
SOURCE=ROOT/'data/research/lan_worker_returns/hft244-corrected-route-fork-2023609-20260910-v1/COMPACT.json'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    data=json.loads(SOURCE.read_text())
    assert data['shamParity'] and data['prefixParity'] and data['pReferenceParity']
    row=next(x for x in data['rows'] if x['branch']=='S')
    assert len(row['receipts'])==2
    manifest=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text())['files']
    hashes={}

    def method(filename, cls, name):
        rel='tools/'+filename; path=FROZEN/rel
        assert digest(path)==manifest[rel]; hashes[rel]=manifest[rel]
        tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
        fn=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name==name)
        ns={'EPS':1e-9}; exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
        return ns[name]

    split=method('run_eth_role_separated_multislot_v8_repair_overflow_split_smoke.py','RepairOverflowSplitSim','_repair_split')
    # The class name is resolved from the selected method's owning class in the frozen V7 file.
    rel='tools/run_eth_role_separated_multislot_v7_monetary_credit_smoke.py'
    tree=ast.parse((FROZEN/rel).read_text(encoding='utf-8-sig'))
    cls=next(n.name for n in tree.body if isinstance(n,ast.ClassDef) and any(
        isinstance(f,ast.FunctionDef) and f.name=='_candidate_alone_floor' for f in n.body))
    floor=method(Path(rel).name,cls,'_candidate_alone_floor')
    inv={s:row[s]+row['cost'] for s in ['UP','DOWN']}
    debt=inv['UP']-inv['DOWN']; assert 0<debt<1
    active=row['selectedActive']; a=active['activePrice']; rq=active['qty']
    assert abs(a*rq-1)<1e-8
    before=row['witness']['state']; assert before['scopeRiskCreditTotal']==1 and before['scopeRiskCreditConsumed']==0
    # Reconstructed from witnessed inputs + frozen service/quarantine equations;
    # NOT an observed terminal serviceLedger snapshot (not saved by route runner).
    minted=rq*(1-a)
    reconstructed_total=before['scopeRiskCreditTotal']+minted-minted
    spent=rq*a
    r247='run_eth_ms4_r2_47_bounded_core_service_favorable_recycle.py'
    rcls='BoundedCoreServiceFavorableRecycleSim'
    claim=method(r247,rcls,'_service_claim')
    credit=method(r247,rcls,'_available_expand_risk_credit')
    authority=SimpleNamespace(scopeSide='UP',scopeGeneration=1,
        scopeRiskCreditTotal=reconstructed_total,scopeRiskCreditConsumed=0.,
        serviceLedger={'DOWN_4':dict(generation=1,scopeSide='UP',held=0.,spent=spent)})
    authority._has_stale_scope_reservation=lambda:False
    authority._reserved_current_expand_risk=lambda:0.
    authority._service_claim=MethodType(claim,authority)
    available=credit(authority)
    assert available<1e-8
    once=method('run_eth_ms4_r2_46_r240_core_active_credit_mechanism_ablation.py',
        'R240OneCoreActiveMechanismSim','_try_core_active')
    assert once(SimpleNamespace(coreActiveMaterialized=True),0) is False
    fixture=SimpleNamespace(inv=inv,cost=row['cost'],splitBlocks=Counter())
    fixture._scope_debt_qty=lambda:debt
    fixture._reserved_repair_quota=lambda side:0.
    fixture._physical_floor=lambda:min(inv.values())-row['cost']
    fixture._available_expand_risk_credit=lambda:available
    fixture._candidate_alone_floor=MethodType(floor,fixture)
    probes=[]
    for p in [.1,.26,.5,.74,.99]:
        fixture.splitBlocks.clear(); result=split(fixture,'DOWN',p,1/p)
        assert result is None and fixture.splitBlocks['OVERFLOW_MONETARY_CREDIT_INSUFFICIENT']==1
        probes.append(dict(price=p,qty=1/p,overflowRisk=1-debt*p,blocked=True))
    # Negative control: same equations, explicitly hypothetical external authority.
    fixture._available_expand_risk_credit=lambda:1.
    assert split(fixture,'DOWN',.74,1/.74) is not None
    result=dict(verdict='CONDITIONAL_RESIDUAL_OPTION_GAP_SUPPORTED_ZERO_ENGINE',
        sourceSha256=digest(SOURCE),frozenHashes=hashes,debt=debt,
        reconstructedAvailableCredit=available,creditStateDirectlyObserved=False,
        favorableLotQty=rq,favorablePriceCeiling=1-a,maxFavorableLotNotional=rq*(1-a),
        venueMinPolicyNotional=1.,pureRepairPossibleAtPolicyMinimum=False,
        passiveOverflowRiskLowerBound=1-debt,repairProbes=probes,
        hypotheticalExternalCreditNegativeControlPass=True,newBE=0,newEngines=0,
        assumption='Zero unspent credit and no additional receipts/authority source after selected service; derived, not a captured final state.',
        scope='Actual frozen repair method on reconstructed fixture plus algebra; not all controller paths certified.',
        recommendation='Test full-lifecycle budget/lot feasibility, do not simply release spent authority or add credit.',
        profitability='NOT_IDENTIFIED',fullNetCost='UNRESOLVED')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
