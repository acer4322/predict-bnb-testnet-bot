from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_instrumented_v1.json'
DEFAULT_OUT = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_journal_audit_v1.json'
EPS = 1e-9


def f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def branch_audit(events: list[dict[str, Any]], state: dict[str, dict[str, Any]], fill_log: list[dict[str, Any]]) -> dict[str, Any]:
    by_resp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        by_resp[str(ev.get('responsibility_id'))].append(ev)

    fill_by_resp_intent: dict[tuple[str, str], float] = defaultdict(float)
    for fl in fill_log:
        if str(fl.get('role')) != 'MAKER' or not fl.get('responsibilityId') or not fl.get('intentId'):
            continue
        fill_by_resp_intent[(str(fl['responsibilityId']), str(fl['intentId']))] += f(fl.get('shares'))

    responsibility_rows = []
    seq_fail = 0
    receive_time_fail = 0
    late_event_time_count = 0
    open_fail = 0
    parent_fail = 0
    state_fail = 0
    remaining_fail = 0
    fill_lineage_fail = 0
    post_completion_carrier_count = 0
    execution_ids: list[str] = []
    orphan_events = 0

    for ev in events:
        rid = str(ev.get('responsibility_id'))
        if rid not in state:
            orphan_events += 1
        if ev.get('execution_id'):
            execution_ids.append(str(ev['execution_id']))

    for rid, evs in by_resp.items():
        st = state.get(rid) or {}
        seqs = [int(e.get('event_seq') or -1) for e in evs]
        seq_ok = seqs == list(range(1, len(evs) + 1))
        if not seq_ok:
            seq_fail += 1
        event_times = [int(e.get('event_at_ms') or 0) for e in evs]
        recv_times = [int(e.get('received_at_ms') or 0) for e in evs]
        receive_time_ok = all(b >= a for a, b in zip(recv_times, recv_times[1:]))
        if not receive_time_ok:
            receive_time_fail += 1
        late_event_time_count += sum(1 for a,b in zip(event_times,event_times[1:]) if b < a)
        opens = [e for e in evs if e.get('event_type') == 'RESPONSIBILITY_OPENED']
        open_ok = len(opens) == 1 and evs[0].get('event_type') == 'RESPONSIBILITY_OPENED'
        if not open_ok:
            open_fail += 1

        intents = [e for e in evs if e.get('event_type') == 'CARRIER_INTENT_CREATED']
        known: list[str] = []
        parent_ok = True
        for i, e in enumerate(intents):
            iid = str(e.get('intent_id'))
            parent = e.get('parent_intent_id')
            if i == 0:
                if parent not in (None, ''):
                    parent_ok = False
            elif parent not in known:
                parent_ok = False
            known.append(iid)
        if not parent_ok:
            parent_fail += 1

        requested = f(opens[0].get('requested_qty')) if opens else f(st.get('requested_qty'))
        # A completed responsibility is terminal. New carrier creation after completion means
        # cancel/fill-race lineage leaked into a later, distinct economic responsibility.
        _complete_idx = next((i for i,e in enumerate(evs) if e.get('event_type') == 'RESPONSIBILITY_COMPLETED'), None)
        _post_completion_carriers = 0 if _complete_idx is None else sum(1 for e in evs[_complete_idx+1:] if e.get('event_type') == 'CARRIER_INTENT_CREATED')
        post_completion_carrier_count += _post_completion_carriers

        fill_events = [e for e in evs if e.get('event_type') in {'PARTIAL_FILL', 'FULL_FILL'}]
        prior_cum = 0.0
        journal_by_intent: dict[str, float] = defaultdict(float)
        monotonic_cum = True
        for e in fill_events:
            cum = f(e.get('cum_confirmed_fill_qty'))
            if cum + EPS < prior_cum:
                monotonic_cum = False
            delta = max(0.0, cum - prior_cum)
            journal_by_intent[str(e.get('intent_id'))] += delta
            prior_cum = max(prior_cum, cum)
        confirmed = prior_cum
        unresolved = max(0.0, requested - confirmed)
        expected_status = 'COMPLETED' if any(e.get('event_type') == 'RESPONSIBILITY_COMPLETED' for e in evs) else 'OPEN'
        state_ok = (
            abs(f(st.get('requested_qty')) - requested) <= EPS
            and abs(f(st.get('confirmed_qty')) - confirmed) <= EPS
            and str(st.get('status')) == expected_status
            and list(st.get('active_intents') or []) == known
            and monotonic_cum
        )
        if not state_ok:
            state_fail += 1
        state_unresolved = max(0.0, f(st.get('requested_qty')) - f(st.get('confirmed_qty')))
        remaining_ok = abs(state_unresolved - unresolved) <= EPS
        if not remaining_ok:
            remaining_fail += 1

        all_intents = set(known) | {iid for (rr, iid) in fill_by_resp_intent if rr == rid}
        fill_ok = True
        per_intent = []
        for iid in sorted(all_intents):
            j = f(journal_by_intent.get(iid))
            l = f(fill_by_resp_intent.get((rid, iid)))
            ok = abs(j - l) <= 1e-7
            if not ok:
                fill_ok = False
            per_intent.append({'intentId': iid, 'journalFillQty': j, 'simulatorFillLogQty': l, 'exact': ok})
        if not fill_ok:
            fill_lineage_fail += 1

        responsibility_rows.append({
            'responsibilityId': rid,
            'eventCount': len(evs),
            'intentCount': len(intents),
            'requestedQty': requested,
            'confirmedQtyReconstructed': confirmed,
            'unresolvedQtyReconstructed': unresolved,
            'sequenceExact': seq_ok,
            'receivedTimeMonotonic': receive_time_ok,
            'lateEventTimeTransitions': sum(1 for a,b in zip(event_times,event_times[1:]) if b < a),
            'singleRootOpen': open_ok,
            'parentChainValid': parent_ok,
            'stateExact': state_ok,
            'remainingExact': remaining_ok,
            'fillLineageExact': fill_ok,
            'postCompletionCarrierCount': _post_completion_carriers,
            'perIntentFillAudit': per_intent,
        })

    duplicate_execution_ids = len(execution_ids) - len(set(execution_ids))
    return {
        'responsibilities': len(by_resp),
        'events': len(events),
        'sequenceFailureCount': seq_fail,
        'receivedTimeFailureCount': receive_time_fail,
        'lateEventTimeTransitionCount': late_event_time_count,
        'rootOpenFailureCount': open_fail,
        'parentChainFailureCount': parent_fail,
        'stateReconstructionFailureCount': state_fail,
        'remainingObligationFailureCount': remaining_fail,
        'fillLineageFailureCount': fill_lineage_fail,
        'duplicateExecutionIdCount': duplicate_execution_ids,
        'orphanEventCount': orphan_events,
        'postCompletionCarrierCount': post_completion_carrier_count,
        'responsibilityRows': responsibility_rows,
    }


def stress_prefix_equal(keep: list[dict[str, Any]], stress: list[dict[str, Any]]) -> dict[str, Any]:
    idx = None
    for i, ev in enumerate(stress):
        if ev.get('event_type') == 'CANCEL_REQUESTED' and (ev.get('extras') or {}).get('exam') == 'P0_DETERMINISTIC_CANCEL_REINSERT_V1':
            idx = i
            break
    if idx is None:
        return {'checkable': False, 'prefixEventCount': 0, 'exact': False}
    prefix = stress[:idx]
    exact = len(keep) >= len(prefix) and keep[:len(prefix)] == prefix
    return {'checkable': True, 'prefixEventCount': len(prefix), 'exact': exact}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=str(DEFAULT_SRC))
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    a = ap.parse_args()
    src = Path(a.src)
    if not src.is_absolute():
        src = ROOT / src
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    d = json.loads(src.read_text(encoding='utf-8'))

    market_rows = []
    total_resp = total_events = 0
    failures = defaultdict(int)
    prefix_pass = prefix_check = 0
    branch_continuity_num = branch_continuity_den = 0
    cancel_resolution_num = cancel_resolution_den = 0

    for r in d.get('rows', []):
        keep = branch_audit(r.get('keepProvenanceJournal') or [], r.get('keepProvenanceResponsibilityState') or {}, r.get('keepFillLog') or [])
        stress = branch_audit(r.get('reinsertProvenanceJournal') or [], r.get('reinsertProvenanceResponsibilityState') or {}, r.get('reinsertFillLog') or [])
        pref = stress_prefix_equal(r.get('keepProvenanceJournal') or [], r.get('reinsertProvenanceJournal') or [])
        if pref['checkable']:
            prefix_check += 1
            if pref['exact']:
                prefix_pass += 1
        for b in (keep, stress):
            total_resp += b['responsibilities']; total_events += b['events']
            for key in ('sequenceFailureCount','receivedTimeFailureCount','rootOpenFailureCount','parentChainFailureCount','stateReconstructionFailureCount','remainingObligationFailureCount','fillLineageFailureCount','duplicateExecutionIdCount','orphanEventCount','postCompletionCarrierCount'):
                failures[key] += int(b[key])
        # Count actual multi-intent roots in stress branch as cancel/reinsert continuity exams.
        for rr in stress['responsibilityRows']:
            if rr['intentCount'] >= 2:
                branch_continuity_den += 1
                if rr['parentChainValid']:
                    branch_continuity_num += 1
        # Every injected cancel must resolve in one of exactly two safe ways:
        # (A) responsibility fills/completes during cancel race; or
        # (B) terminal cancel is followed by a new carrier on the SAME root linked to old intent.
        stress_evs = r.get('reinsertProvenanceJournal') or []
        for se in (r.get('provenanceStressEvents') or []):
            cancel_resolution_den += 1
            rid = str(se.get('responsibilityId')); old_iid = str(se.get('oldIntentId')); at = int(se.get('cancelRequestedAtMs') or 0)
            future = [e for e in stress_evs if str(e.get('responsibility_id')) == rid and int(e.get('received_at_ms') or 0) >= at]
            completed = any(e.get('event_type') == 'RESPONSIBILITY_COMPLETED' for e in future)
            replaced = any(e.get('event_type') == 'CARRIER_INTENT_CREATED' and str(e.get('parent_intent_id')) == old_iid for e in future)
            if completed or replaced:
                cancel_resolution_num += 1
        market_rows.append({'marketId': r.get('marketId'), 'keep': keep, 'stress': stress, 'preStressPrefix': pref, 'stressCancelEvents': len(r.get('provenanceStressEvents') or [])})

    exact_reconstruction = failures['stateReconstructionFailureCount'] == 0 and failures['remainingObligationFailureCount'] == 0
    exact_fill_lineage = failures['fillLineageFailureCount'] == 0 and failures['duplicateExecutionIdCount'] == 0
    structural_exact = all(failures[k] == 0 for k in ('sequenceFailureCount','receivedTimeFailureCount','rootOpenFailureCount','parentChainFailureCount','orphanEventCount','postCompletionCarrierCount'))
    prefix_rate = prefix_pass / max(1, prefix_check)
    branch_rate = branch_continuity_num / max(1, branch_continuity_den)
    cancel_resolution_rate = cancel_resolution_num / max(1, cancel_resolution_den)
    smoke_pass = (
        len(market_rows) >= 5 and total_resp > 0 and structural_exact and exact_reconstruction and exact_fill_lineage
        and prefix_check == len(market_rows) and prefix_rate == 1.0
        and branch_continuity_den >= len(market_rows) and branch_rate == 1.0
        and cancel_resolution_den >= len(market_rows) and cancel_resolution_rate == 1.0
    )
    rep = {
        'version': 'R4_P0_PROVENANCE_JOURNAL_AUDIT_V1',
        'source': str(src.relative_to(ROOT)).replace('\\','/'),
        'status': 'SMOKE_PASS' if smoke_pass else 'SMOKE_FAIL',
        'scope': 'P0-A research-only deterministic lifecycle instrumentation; not strategy/action authority',
        'summary': {
            'markets': len(market_rows),
            'responsibilitiesAudited': total_resp,
            'eventsAudited': total_events,
            'preStressPrefixExactRate': prefix_rate,
            'branchContinuityRate': branch_rate,
            'branchContinuityChecks': branch_continuity_den,
            'cancelResolutionExactRate': cancel_resolution_rate,
            'cancelResolutionChecks': cancel_resolution_den,
            'stateReconstructionExact': exact_reconstruction,
            'fillLineageExact': exact_fill_lineage,
            'structuralExact': structural_exact,
            **dict(failures),
        },
        'smokeGate': {
            'minimumMarkets': 5,
            'allStructuralFailuresZero': True,
            'stateAndRemainingExact': True,
            'fillLineageExact': True,
            'preStressPrefixExactRate': 1.0,
            'branchContinuityRate': 1.0,
            'cancelResolutionExactRate': 1.0,
            'postCompletionCarrierCount': 0,
        },
        'markets': market_rows,
        'guards': {'researchOnly': True, 'noLiveR3Change': True, 'no8781Change': True, 'noEchtgeld': True, 'special20260816Sealed': True},
        'nextIfPass': 'Run fixed-repeat determinism and then >=20 fresh receipt-clock market independent provenance validation before reopening any queue-option belief.',
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': rep['status'], 'artifact': str(out.relative_to(ROOT)).replace('\\','/'), 'summary': rep['summary']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
