from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_management_mainline_v3b_closed_loop_role_manager_v1 as mgmt

ACTIONS = ("NATIVE", "HOLD", "REPAIR", "REEXPAND")


class NeedDecision(RuntimeError):
    def __init__(self, snapshot: dict):
        super().__init__("NEED_DECISION")
        self.snapshot = snapshot


def _r(x, n=6):
    try:
        return round(float(x), n)
    except Exception:
        return x


class BlindManualManager(mgmt.ClosedLoopManager):
    """
    Research-only strict-past management replay.

    Execution, queue, partial-fill, ownership and legality stay in the inherited
    exact-HFT substrate.  Only the management choice at an already-legal seam is
    exposed to the operator/model.

    Decisions:
      NATIVE    - use the inherited V3B decision for this seam
      HOLD      - skip opening a new option at this seam
      REPAIR    - force the currently legal repair-side candidate
      REEXPAND  - force the currently legal re-expansion candidate

    No Target action, winner, settlement or future state enters the decision view.
    """

    def __init__(self, tape: Path, decisions: list[str]):
        super().__init__(tape, "NATIVE")
        self.manual_decisions = [str(x).upper() for x in decisions]
        bad = [x for x in self.manual_decisions if x not in ACTIONS]
        if bad:
            raise ValueError(f"unsupported decisions: {bad}")
        self.manual_index = 0
        self.manual_log: list[dict] = []
        self._last_prompt_key = None

    def _prompt_key(self, e: dict) -> tuple:
        # State/event driven de-duplication only.  This is UI/replay bookkeeping,
        # not a strategy clock or decision threshold.
        return (
            str(e.get("weakSide")),
            str(e.get("expandSide")),
            int(e.get("freeSlots") or 0),
            round(float(e.get("remainingDebtQty") or 0.0), 6),
            round(float(e.get("repairProgressFrac") or 0.0), 6),
            int(self.fills),
            int(self.submits),
            len(self.resp_all),
            str((self.q_ladder or {}).get("route")),
            str((self.q_pending_active or {}).get("side")),
        )

    def _snapshot(self, t: int, qv: dict, end: int, e: dict) -> dict:
        state, held, weak = self._state()
        inv = {s: float(self.inv[s]) for s in ("UP", "DOWN")}
        cost = float(self.cost)
        payoff = {s: inv[s] - cost for s in ("UP", "DOWN")}
        live = []
        for sid, key in sorted(self.slot_key.items()):
            o = self.orders.get(key)
            if not o:
                continue
            live.append({
                "slot": int(sid),
                "key": key,
                "side": str(o.get("side")),
                "role": str(self.key_role.get(key, "UNASSIGNED")),
                "price": _r(o.get("price")),
                "qty": _r(o.get("qty")),
                "cum": _r(o.get("cum")),
                "status": str(o.get("status")),
                "cancelRequested": bool(o.get("cancelRequested")),
            })
        resp = []
        for x in self.resp_all[-12:]:
            resp.append({
                "id": int(x.get("id") or 0),
                "side": str(x.get("side")),
                "price": _r(x.get("price")),
                "initialQty": _r(x.get("initialQty")),
                "paidQty": _r(x.get("paidQty")),
                "remainingQty": _r(x.get("remainingQty")),
                "bornAt": int(x.get("bornAt") or 0),
            })
        fills = []
        for x in self.fill_accounting[-8:]:
            fills.append({
                "t": int(x.get("t") or 0),
                "side": str(x.get("side")),
                "confirmedQty": _r(x.get("confirmedQty")),
                "executionPrice": _r(x.get("executionPriceFromInheritedSubstrate")),
                "matchedRepairQty": _r(x.get("matchedRepairQty")),
                "overflowQty": _r(x.get("overflowQty")),
            })

        def qside(s: str) -> dict:
            z = qv[s]
            return {"bid": _r(z["bid"]), "ask": _r(z["ask"])}

        return {
            "version": "BTC5M_LLM_BLIND_MANAGEMENT_REPLAY_V1",
            "strictPast": True,
            "decisionIndex": int(self.manual_index),
            "sourceReceivedMs": int(t),
            "remainingMs": int(end) - int(t),
            "remainingSec": _r((int(end) - int(t)) / 1000.0, 3),
            "portfolio": {
                "state": state,
                "heldSide": held,
                "weakSide": weak,
                "inventory": inv,
                "cost": _r(cost),
                "payoffIfSettleNow": {k: _r(v) for k, v in payoff.items()},
                "floor": _r(min(payoff.values())),
                "best": _r(max(payoff.values())),
            },
            "book": {"UP": qside("UP"), "DOWN": qside("DOWN")},
            "managementSeam": {
                "weakSide": str(e["weakSide"]),
                "expandSide": str(e["expandSide"]),
                "repairProgressFrac": _r(e["repairProgressFrac"]),
                "remainingDebtQty": _r(e["remainingDebtQty"]),
                "freeSlots": int(e["freeSlots"]),
                "repairCandidate": {
                    "side": str(e["repairCandidate"]["side"]),
                    "price": _r(e["repairCandidate"]["price"]),
                    "qty": _r(e["repairCandidate"]["qty"]),
                },
                "expandCandidate": {
                    "side": str(e["expandCandidate"]["side"]),
                    "price": _r(e["expandCandidate"]["price"]),
                    "qty": _r(e["expandCandidate"]["qty"]),
                },
            },
            "liveOrders": live,
            "recentResponsibilities": resp,
            "recentConfirmedFills": fills,
            "counts": {
                "submits": int(self.submits),
                "fills": int(self.fills),
                "liveSlots": int(len(self.slot_key)),
                "responsibilities": int(len(self.resp_all)),
            },
            "allowedActions": list(ACTIONS),
            "hiddenUntilEnd": [
                "Target actions",
                "Target inventory",
                "winner/settlement",
                "future book",
                "future fills",
                "native-policy choice at this seam",
            ],
        }

    def _apply_manual_choice(self, t: int, qv: dict, end: int, e: dict, choice: str):
        before = {"submits": int(self.submits), "fills": int(self.fills)}
        detail = None
        if choice == "HOLD":
            ok = True
        elif choice == "NATIVE":
            mgmt.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self, t, qv, end)
            ok = True
        else:
            ok, side, role, cand = self._force_one(
                t, qv, "REPAIR" if choice == "REPAIR" else "REEXPAND"
            )
            detail = {"side": side, "role": role, "candidate": cand}
        self.manual_log.append({
            "decisionIndex": int(self.manual_index),
            "t": int(t),
            "remainingMs": int(end) - int(t),
            "choice": choice,
            "ok": bool(ok),
            "detail": detail,
            "before": before,
            "after": {"submits": int(self.submits), "fills": int(self.fills)},
            "strictPastSeam": {
                "weakSide": str(e["weakSide"]),
                "expandSide": str(e["expandSide"]),
                "repairProgressFrac": float(e["repairProgressFrac"]),
                "remainingDebtQty": float(e["remainingDebtQty"]),
                "freeSlots": int(e["freeSlots"]),
            },
        })
        self.manual_index += 1

    def _open_one_option(self, t, qv, end):
        e = self._eligible(t, qv, end)
        if e is None:
            return mgmt.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(
                self, t, qv, end
            )

        key = self._prompt_key(e)
        # After a HOLD / failed forced action, do not create repeated prompts on
        # the exact same state.  As soon as actual fills/submits/debt/lifecycle
        # changes, the key changes and a new decision epoch is exposed.
        if key == self._last_prompt_key:
            return None

        self._last_prompt_key = key
        if self.manual_index >= len(self.manual_decisions):
            raise NeedDecision(self._snapshot(t, qv, end, e))

        self._apply_manual_choice(
            t, qv, end, e, self.manual_decisions[self.manual_index]
        )
        return None


def read_decisions(path: str | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d, dict):
        d = d.get("decisions", [])
    if not isinstance(d, list):
        raise ValueError("decisions JSON must be a list or {decisions:[...]}")
    return [str(x).upper() for x in d]


def write_json(path: str, obj: dict):
    if str(path).upper() == "AUTO":
        p = Path(os.environ["BTC5M_LAN_RESULT_DIR"]) / "result.json"
    else:
        p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--decisions-json")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    tape = Path(args.tape)
    decisions = read_decisions(args.decisions_json)
    sim = BlindManualManager(tape, decisions)
    try:
        try:
            r = sim.run_managed()
        except NeedDecision as nd:
            out = {
                "status": "NEED_DECISION",
                "researchOnly": True,
                "runtimeAuthority": False,
                "nativeHftExecution": True,
                "tape": tape.name,
                "providedDecisions": decisions,
                "decisionLog": sim.manual_log,
                "next": nd.snapshot,
                "boundary": [
                    "strict-past operator view only",
                    "Target/winner/future hidden",
                    "management choice only; inherited execution semantics",
                    "no 8781/live changes",
                ],
            }
            write_json(args.output, out)
            print(json.dumps({
                "ok": True,
                "status": "NEED_DECISION",
                "decisionIndex": nd.snapshot["decisionIndex"],
                "remainingSec": nd.snapshot["remainingSec"],
                "floor": nd.snapshot["portfolio"]["floor"],
                "best": nd.snapshot["portfolio"]["best"],
            }, ensure_ascii=False))
            return

        out = {
            "status": "COMPLETE",
            "researchOnly": True,
            "runtimeAuthority": False,
            "nativeHftExecution": True,
            "tape": tape.name,
            "providedDecisions": decisions,
            "decisionLog": sim.manual_log,
            "final": {
                "upQty": float(r["upQty"]),
                "downQty": float(r["downQty"]),
                "buyNotional": float(r["buyNotional"]),
                "payoffUP": float(r["upQty"]) - float(r["buyNotional"]),
                "payoffDOWN": float(r["downQty"]) - float(r["buyNotional"]),
                "floor": float(r["floor"]),
                "best": float(r["best"]),
                "fills": int(r["fillEvents"]),
                "submits": int(r["submits"]),
                "ledgerViolations": (r.get("quantityLedgerSummary") or {}).get("invariantViolations") or {},
            },
            "boundary": [
                "winner remains hidden/not needed for completion",
                "Target actions remain hidden",
                "no 8781/live changes",
            ],
        }
        write_json(args.output, out)
        print(json.dumps({
            "ok": True,
            "status": "COMPLETE",
            "decisions": len(sim.manual_log),
            "floor": out["final"]["floor"],
            "best": out["final"]["best"],
            "fills": out["final"]["fills"],
        }, ensure_ascii=False))
    finally:
        sim.close()


if __name__ == "__main__":
    main()
