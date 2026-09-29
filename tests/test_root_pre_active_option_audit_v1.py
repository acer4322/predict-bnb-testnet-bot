from collections import Counter
import pytest
from tools import run_root_pre_active_option_audit_v1 as audit


class FakeBase:
    def __init__(self):
        self.bt = object()
        self.veto = Counter(); self.splitBlocks = Counter(); self.role_budget_blocks = Counter()
        self.r239 = Counter(); self.r247 = Counter(); self.allowed = True

    def _open_one_option(self, t, quotes, end):
        if not self.allowed:
            self.veto['BLOCK'] += 1
            return
        self._submit_role_v8(t, 'UP', 'SATELLITE_REPAIR', .5, 2, 0,
                             {'repairQty': 2, 'overflowQty': 0})

    def _submit_role_v8(self, t, side, role, p, q, proj, split):
        self.submit(t, side, p, q)
        raise AssertionError('must stop before post-submit accounting')


def test_probe_stops_transport_and_preserves_source():
    sim = FakeBase(); before = audit.lab.signature(sim)
    p = audit.passive_probe(sim, FakeBase, 7, {}, 1000)
    assert p['status'] == 'TRANSPORT_PROPOSAL_ONLY'
    assert p['candidate']['qty'] == 2
    assert audit.lab.signature(sim) == before


def test_no_proposal_preserves_original_counter():
    sim = FakeBase(); sim.allowed = False
    p = audit.passive_probe(sim, FakeBase, 7, {}, 1000)
    assert p['status'] == 'NO_NATIVE_TRANSPORT_PROPOSAL'
    assert p['counterDelta']['veto'] == {'BLOCK': 1}
    assert not sim.veto


def test_backend_writes_forbidden():
    with pytest.raises(RuntimeError, match='preview forbids'):
        audit.lab.ReadOnlyBackend(object()).submit_buy_order


@pytest.mark.parametrize('accepted,overflow,side,expected', [
    (True, 0, 'UP', True), (False, 0, 'UP', False),
    (True, .1, 'UP', False), (True, 0, 'DOWN', False)])
def test_only_accepted_same_side_pure_repair_is_matched(accepted, overflow, side, expected):
    p = audit.passive_probe(FakeBase(), FakeBase, 7, {}, 1000)
    p['candidate']['split']['overflowQty'] = overflow
    w = dict(activeAccepted=accepted, activeSide=side, passive=p)
    assert audit.matched_repair(w) == expected
