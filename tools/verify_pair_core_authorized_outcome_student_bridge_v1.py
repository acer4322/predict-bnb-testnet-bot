"""Bounded local contract verification. No market replay, worker job or training."""
from pathlib import Path
import hashlib
import io
import json
import unittest

from tools.minimal_student_open_funding_v1 import OpenFundingLedger, OpenFundingProfile, self_tests
from tools.minimal_student_system_plan_v1 import PlanAction
from tools.pair_core_economic_grant_ledger_v1 import Grant
from tools.pair_core_authorized_outcome_preview_v1 import InitialPortfolio, EndpointAuthority
from tools.pair_core_authorized_outcome_student_bridge_v1 import AuthorizedOutcomeStudentGateway


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/r4_v0/p0_provenance_v1/pair_core_authorized_outcome_student_bridge_v1_20260911'
FROZEN = {
    'tools/pair_core_economic_grant_ledger_v1.py': '6ee5dffd59fa45a48f2b18e8a592b1922c9e5202116e2712f5e31df4d72fea0c',
    'tools/pair_core_asset_route_sizing_v2.py': 'aac866e856748d95c0551f8366576e65e2b30139c8b6ef8806d08f83b7982ef8',
    'tools/minimal_student_open_funding_v1.py': '507bd3724ec534e6c9c64a479aa3985f77304f796529bad765780e9e538ca1dd',
    'tools/minimal_student_training_world_v2.py': 'e1c231f255ed8c77dc4f9a37464ec3960edc44effa7bd21b42ac170af608fa34',
    'tools/pair_core_authorized_outcome_preview_v1.py': '62e8b1fe1dbba335def8f4c44ecf3cc8a1fd8bac90df97b794531cf336b88546',
}
MODULES = [
    'tests.test_pair_core_authorized_outcome_student_bridge_v1',
    'tests.test_pair_core_authorized_outcome_preview_v1',
    'tests.test_pair_core_asset_route_sizing_v2',
    'tests.test_pair_core_economic_grant_ledger_v1',
    'tests.test_pair_core_objective_quantity_planner_v1',
    'tests.test_pair_core_route_value_contract_v1',
    'tests.test_minimal_student_training_world_v2',
    'tests.test_minimal_student_native_system_plan_v1',
]


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def witness():
    l = OpenFundingLedger(OpenFundingProfile())
    l.issue(Grant(1, 'REPAIR_FIXTURE', 'DOWN', 24., 0., 0., 'EXTERNAL_TEST_REPAIR'))
    l.issue(Grant(2, 'ADD_FIXTURE', 'UP', 0., 0., 0., 'EXTERNAL_TEST_ADD'))
    g = AuthorizedOutcomeStudentGateway(l, policy_id='EXPLICIT_FIXTURE',
        initial=InitialPortfolio(120.,80.,85.), bounds=EndpointAuthority(0.,-6.,'EXTERNAL_TEST_BOUNDS'))
    r = PlanAction('NEW','DOWN_1',1,'PASSIVE',.19,24.)
    a = PlanAction('NEW','UP_2',2,'PASSIVE',.62,18.)
    times = dict(now_ms=200000, market_end_ms=300000)
    joint = g.propose('joint',(r,a),'SAME_TWO_GOALS')
    before = g.preview_plan(joint, **times)
    assert not before['accepted']
    g.commit(g.propose('repair',(r,),'SAME_TWO_GOALS'), **times)
    g.record_send('DOWN_1','SENT',evidence='SYNTHETIC_TRANSPORT_ONLY')
    g.receipt('DOWN_1',filled=15.,payment=2.85,evidence='SYNTHETIC_PARTIAL_ONLY')
    add_plan = g.propose('add',(PlanAction('KEEP','DOWN_1'),a),'SAME_TWO_GOALS')
    after = g.preview_plan(add_plan, **times)
    assert after['accepted']
    g.commit(add_plan, **times)
    partial = g.own_state()
    g.commit(g.propose('cancel',(PlanAction('CANCEL','DOWN_1'),PlanAction('KEEP','UP_2')),
                       'SAME_TWO_GOALS'), **times)
    cancelled = g.own_state()
    assert g.ledger.account(1)['reserved_qty'] == 9.
    g.receipt('DOWN_1',filled=15.,payment=2.85,terminal=True,evidence='SYNTHETIC_TERMINAL_ONLY')
    terminal = g.own_state()
    assert g.ledger.account(1)['repair_remaining'] == 9.
    assert g.ledger.account(1)['reserved_qty'] == 0.
    return dict(evidence_type='SYNTHETIC_INTERFACE_FIXTURE_NOT_NATIVE_OR_TARGET',
        before_partial=before, after_partial=after, committed_partial=partial,
        cancel_pending=cancelled, repair_terminal_add_still_reserved=terminal)


def main():
    unchanged = {p: sha(p) for p in FROZEN}
    assert unchanged == FROZEN, 'Existing source drift: reassess instead of silently reusing results'
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    if not result.wasSuccessful():
        print(stream.getvalue())
        raise SystemExit(1)
    funding = self_tests()
    example = witness()
    new_paths = ['tools/pair_core_authorized_outcome_student_bridge_v1.py',
                 'tests/test_pair_core_authorized_outcome_student_bridge_v1.py',
                 'tools/verify_pair_core_authorized_outcome_student_bridge_v1.py']
    compact = dict(
        status='KEEP_COMPONENT_BRIDGE_NATIVE_VALIDATION_PENDING',
        unit_tests=result.testsRun, open_funding_assertions=funding['passed'],
        total_checks=result.testsRun+funding['passed'], new_bridge_tests=25,
        native_matching_runs=0, trained_models=0, worker_jobs_launched=0,
        target_private_authority_recovered=False, native_active_supported=False,
        funding_mode='VIRTUAL_NONBINDING_RESEARCH', capital_cap=None,
        source_hashes_unchanged=unchanged,
        new_source_hashes={p:sha(p) for p in new_paths},
        limitations=[
            'Synthetic receipts and a deterministic boundary double are not native matching evidence.',
            'Factory is opt-in; no existing student runner or live controller is changed.',
            'Factory requires a flat native start; nonzero seed is gateway-fixture-only.',
            'Existing native zero-fill audit mismatch and terminal/resource gaps remain unresolved.',
            'Native ACTIVE remains unsupported, with explicit failure rather than HOLD.',
            'Endpoint authority is externally supplied per experiment, never a default training cap.',
            'No target goal/quantity/risk learning, profitability or full-market acceptance.',
            'Gateway copies full owner state; no latency or HFT performance acceptance.',
        ])
    verification = dict(test_modules=MODULES, test_output=stream.getvalue(),
                        open_funding_assertions=funding, source_hashes_unchanged=unchanged)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in [('COMPACT.json',compact),('VERIFICATION.json',verification),('CONTROL_WITNESS.json',example)]:
        (OUT / name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    assert {p:sha(p) for p in FROZEN} == unchanged
    print(json.dumps(dict(status=compact['status'],unit_tests=result.testsRun,
                         funding_assertions=funding['passed'],total_checks=compact['total_checks'],
                         output=str(OUT)),ensure_ascii=False))


if __name__ == '__main__':
    main()
