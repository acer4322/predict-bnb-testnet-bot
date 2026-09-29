"""Explicitly authorized local replay adapter; never a live or worker launcher."""
from __future__ import annotations

import contextlib
import contextvars
import importlib
import importlib.metadata
import json
import lzma
import os
import sys
from pathlib import Path

from .core import EconomicLedger, TOL, canonical, digest, file_digest, number, read_json, write_json_new

ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / "data/research/mvps_research_v1/runs"
CONSUMED_MARKETS = (1945866, 1945869, 1945898, 1945986)
PROFILES = {"pair1_native": 1, "pair4_native": 4}
LEGACY_SOURCES = (
    "tools/run_eth_safety_reintroduction_ladder_1946317.py",
    "tools/run_eth_dagger60_smoke_v1.py",
    "tools/hftbacktest_execution_shift_audit_v0.py",
    "tools/hftbacktest_execution_tape_feed_v1.py",
    "tools/hftbacktest_true_match_calibration_v0.py",
    "src/predict_bot/execution_tape_archive_v1.py",
)
OWN_SOURCES = tuple("tools/mvps_research_v1/" + x for x in
                    ("__init__.py", "core.py", "runner.py", "cli.py")) + ("tools/run_mvps_research_system_v1.py",)

_OFFLINE = contextvars.ContextVar("mvps_research_offline", default=False)


def _audit(event, args):
    if _OFFLINE.get() and event in {"socket.connect", "socket.bind", "socket.getaddrinfo",
                                    "sqlite3.connect", "subprocess.Popen", "os.system"}:
        raise PermissionError(f"research replay forbids external/DB execution: {event}")


sys.addaudithook(_audit)


@contextlib.contextmanager
def offline_boundary():
    token = _OFFLINE.set(True)
    try:
        yield
    finally:
        _OFFLINE.reset(token)


def source_pins():
    return {name: file_digest(ROOT / name) for name in LEGACY_SOURCES + OWN_SOURCES}


def draft_plan():
    return {
        "schema": "MVPS_RESEARCH_PLAN_V1", "researchOnly": True, "liveAuthority": False,
        "cohortKind": "consumed_development", "policyId": "pair1_native",
        "marketIds": list(CONSUMED_MARKETS), "tapes": {}, "settlements": None,
        "sourcePins": {}, "backendVersions": {}, "backendFilePins": {},
        "evaluationContract": {
            "costScope": "ALL_COSTS_NOT_ALREADY_IN_BUY_NOTIONAL",
            "extraCostUpperByMarket": None, "costProvenance": None,
            "riskLimits": {"maxWorstEndpointLoss": None, "maxReceiptPeakAbsNet": None,
                           "maxTerminalSequenceDrawdown": None, "maxCashPlusReservations": None}, "riskProvenance": None,
            "minActivityRetention": {k: None for k in
                                     ("fillEvents", "buyNotional", "fillSideAlternations", "pairLinks", "absNetShareMs")},
            "minTradeCoverage": None, "activityProvenance": None,
        },
        "activityControl": None,
        "boundary": "DRAFT: no replay authorization; no automatic use of historic 2 BE",
    }


def validate_plan(plan, *, verify_files=False):
    if plan.get("schema") != "MVPS_RESEARCH_PLAN_V1":
        raise ValueError("unknown plan schema")
    if plan.get("researchOnly") is not True or plan.get("liveAuthority") is not False:
        raise ValueError("research-only authority required")
    if plan.get("cohortKind") != "consumed_development":
        raise ValueError("fresh/held-out cohorts are locked")
    if plan.get("policyId") not in PROFILES:
        raise ValueError("unsupported policy; no arbitrary code plugins")
    mids = plan.get("marketIds")
    if not isinstance(mids, list) or not mids or len(set(mids)) != len(mids):
        raise ValueError("empty or duplicate cohort")
    if any(type(m) is not int or m not in CONSUMED_MARKETS for m in mids):
        raise ValueError("market outside initial consumed-development allowlist")
    if mids != list(CONSUMED_MARKETS[:len(mids)]):
        raise ValueError("use a fixed prefix, not a post-result selected subset")
    if set(plan.get("tapes", {})) != {str(m) for m in mids}:
        raise ValueError("tape manifest must exactly cover every assigned market")
    if not isinstance(plan.get("evaluationContract"), dict):
        raise ValueError("missing evaluation contract")
    if verify_files:
        if plan.get("sourcePins") != source_pins():
            raise ValueError("source drift: freeze a new research plan, never silently continue")
        for mid in mids:
            item = plan["tapes"][str(mid)]
            path = Path(item["path"])
            if path.name != f"{mid}.json.xz" or file_digest(path) != item["sha256"]:
                raise ValueError(f"tape identity/hash mismatch: {mid}")
            with lzma.open(path, "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("marketId") != mid:
                raise ValueError("embedded market identity mismatch; do not load another archive")
            del payload
        settlement = plan.get("settlements")
        if not settlement or file_digest(settlement["path"]) != settlement["sha256"]:
            raise ValueError("evaluation-only settlement artifact missing or changed")
        if set(plan.get("backendVersions", {})) != {"hftbacktest", "numpy", "numba"}:
            raise ValueError("backend versions must be frozen explicitly")
        if not plan.get("backendFilePins"):
            raise ValueError("pin backend package entry and native binaries before replay")
        for path, sha in plan["backendFilePins"].items():
            if file_digest(path) != sha:
                raise ValueError("backend file hash mismatch")
    return 2 * len(mids)  # unmodified control + instrumented same-policy parity


def validate_authorization(plan, authorization):
    required = validate_plan(plan)
    if authorization.get("allowReplay") is not True:
        raise ValueError("replay requires a new explicit authorization")
    if authorization.get("planSha256") != digest(plan):
        raise ValueError("authorization does not match frozen plan")
    pool = authorization.get("budgetPool")
    if (not isinstance(pool, str) or not pool.startswith("MVPS_RESEARCH_V1_NEW_")
            or not authorization.get("authorizationRef")):
        raise ValueError("new budget pool and user authorization reference required; historic 2 BE locked")
    budget = authorization.get("maxBE")
    if type(budget) is not int or budget < required or budget > 8:
        raise ValueError(f"this bounded stage requires {required} BE; stage ceiling is 8")
    return required


class BudgetJournal:
    """Reserve before constructors can touch tape. Failure consumes the reservation.

    Persistent usage is keyed by budget pool, so editing an authorization file
    does not reset consumed BE. A stale lock requires human inspection, not retry.
    """
    def __init__(self, directory, authorization):
        self.directory = Path(directory)
        self.authorization = authorization
        self.key = digest(authorization["budgetPool"])

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = self.directory / (self.key + ".lock")
        self.lock_handle = self.lock.open("x", encoding="utf-8")
        self.path = self.directory / (self.key + ".jsonl")
        try:
            self.used = 0
            if self.path.exists():
                with self.path.open(encoding="utf-8") as stream:
                    for line in stream:
                        row = json.loads(line)
                        if row["event"] == "BE_RESERVED":
                            self.used += 1
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def reserve(self, market_id, branch):
        if self.used >= self.authorization["maxBE"]:
            raise ValueError("BE budget exhausted; no automatic retry")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(canonical({"event": "BE_RESERVED", "marketId": market_id,
                                    "branch": branch, "ordinal": self.used + 1,
                                    "planSha256": self.authorization["planSha256"]}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.used += 1

    def __exit__(self, *args):
        self.lock_handle.close()
        self.lock.unlink()  # one exact, owned lock file; no recursive deletion


class TraceSink:
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=False)
        self.counts = {}
        self.handles = {}

    def emit(self, kind, row):
        if kind not in self.handles:
            self.handles[kind] = (self.path / f"{kind}.jsonl").open("x", encoding="utf-8")
        self.handles[kind].write(canonical(row) + "\n")
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def close(self):
        for stream in self.handles.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()

    def manifest(self):
        return {kind: {"rows": self.counts[kind], "sha256": file_digest(self.path / f"{kind}.jsonl")}
                for kind in self.handles}


def instrumented_class(base_class):
    """Observe existing calls only: no extra exchange snapshots or policy vetoes."""
    class Instrumented(base_class):
        def __init__(self, tape, slots, sink):
            self.research_sink = sink
            self.research_snapshot = None
            self.research_orders = {}
            self.research_cash_reservation_peak = 0.0
            super().__init__(tape, slots, True, False, False)
            self.research_ledger = EconomicLedger(int(self.meta["firstReceivedMs"]), sink.emit)

        def snap(self, order):
            snapshot = super().snap(order)
            self.research_snapshot = (dict(order), dict(snapshot))
            return snapshot

        def record_fill(self, t, side, q, p):
            order, snapshot = self.research_snapshot
            if order["side"] != side:
                raise ValueError("snapshot-to-fill identity mismatch")
            super().record_fill(t, side, q, p)
            cumulative = snapshot.get("cumExecQty")
            oid = f"{side}_{order['n']}"
            self.research_ledger.fill({
                "fillId": f"{oid}:{cumulative}", "orderId": oid,
                "receiptMs": int(t), "placedMs": int(order["placed"]),
                "side": side, "qty": str(q), "price": str(p),
                "orderQty": str(order["qty"]), "cumQty": str(cumulative),
                "priceAuthority": "EXCHANGE_SNAPSHOT" if snapshot.get("execPrice") is not None
                                  else "INHERITED_LIMIT_FALLBACK",
                "observedStatus": snapshot.get("status"),
                "nativeSnapshot": snapshot,
            })

        def capture_orders(self, t):
            reserved = sum(max(0.0, float(self.orders[k]["qty"]) - float(self.orders[k]["cum"]))
                           * float(self.orders[k]["price"]) for k in self.slot_key.values())
            self.research_cash_reservation_peak = max(self.research_cash_reservation_peak, self.cost + reserved)
            for key, order in self.orders.items():
                copy = dict(order)
                if copy != self.research_orders.get(key):
                    self.research_sink.emit("orders", {"receiptMs": int(t), "orderId": key,
                                                       "localOrderState": copy})
                    self.research_orders[key] = copy

        def process(self, t):
            super().process(t)
            self.capture_orders(t)

        def _open_free_slots(self, t, qv, end):
            self.research_sink.emit("decisions", {
                "receiptMs": int(t), "endMs": int(end), "publicQuote": qv,
                "ownInventory": dict(self.inv), "buyNotional": self.cost,
                "slotOwnership": dict(self.slot_key), "nextNativeOrderId": self.n,
            })
            result = super()._open_free_slots(t, qv, end)
            self.capture_orders(t)
            return result

        def _refresh_slots(self, t):
            before = len(self.slot_history)
            result = super()._refresh_slots(t)
            for event in self.slot_history[before:]:
                self.research_sink.emit("slot_releases", dict(event))
            return result
    return Instrumented


def behavior_state(sim, result):
    return {"result": result, "orders": sim.orders,
            "slotHistory": sim.slot_history, "slotKey": sim.slot_key,
            "inventory": sim.inv, "cash": sim.cost, "veto": dict(sim.veto)}


def reconcile(ledger, result):
    for key in ("upQty", "downQty", "buyNotional", "fillEvents"):
        if abs(number(ledger[key]) - number(result[key])) > TOL:
            raise ValueError(f"native/observer accounting mismatch: {key}")


def load_backend(plan):
    module = importlib.import_module("tools.run_eth_safety_reintroduction_ladder_1946317")
    # The imported legacy base contains unrelated training functions; none are called.
    expected_modules = {
        module: "tools/run_eth_safety_reintroduction_ladder_1946317.py",
        module.base: "tools/run_eth_dagger60_smoke_v1.py",
        module.base.ex: "tools/hftbacktest_execution_shift_audit_v0.py",
        module.base.feed: "tools/hftbacktest_execution_tape_feed_v1.py",
        module.base.feed.tm: "tools/hftbacktest_true_match_calibration_v0.py",
    }
    for item, path in expected_modules.items():
        if Path(item.__file__).resolve() != (ROOT / path).resolve():
            raise ValueError("unexpected staged or shadowed backend module")
    for name, expected in plan["backendVersions"].items():
        pkg = importlib.import_module(name)
        actual = getattr(pkg, "__version__", None)
        if actual is None:
            actual = importlib.metadata.version(name)
        if str(actual) != str(expected):
            raise ValueError(f"backend version drift: {name}")
    hbt_init = str(Path(module.base.ex.hbt.__file__).resolve())
    if hbt_init not in plan["backendFilePins"]:
        raise ValueError("actual hftbacktest entry is not pinned")
    package_dir = Path(hbt_init).parent
    native_files = list(package_dir.glob("*.so")) + list(package_dir.glob("*.pyd"))
    if not native_files or any(str(p.resolve()) not in plan["backendFilePins"] for p in native_files):
        raise ValueError("actual hftbacktest native binary is not pinned")
    return module.LadderSim


def replay(plan, authorization, output):
    validate_authorization(plan, authorization)  # before tape reads or heavy imports
    validate_plan(plan, verify_files=True)
    output = Path(output).resolve()
    if output == RUN_ROOT.resolve() or not output.is_relative_to(RUN_ROOT.resolve()):
        raise ValueError("output must be a new child of the dedicated research runs directory")
    output.mkdir(parents=True, exist_ok=False)
    write_json_new(output / "plan.json", plan)
    write_json_new(output / "authorization.json", authorization)
    write_json_new(output / "STARTED.json", {"status": "INCOMPLETE_UNTIL_COMPLETED", "planSha256": digest(plan)})
    rows = []
    try:
        with offline_boundary(), BudgetJournal(RUN_ROOT / "_budget_journal", authorization) as budget:
            if budget.used + 2 * len(plan["marketIds"]) > authorization["maxBE"]:
                raise ValueError("insufficient unspent budget for the complete fixed cohort")
            cls = load_backend(plan)
            decorated = instrumented_class(cls)
            for mid in plan["marketIds"]:
                tape = Path(plan["tapes"][str(mid)]["path"])
                slots = PROFILES[plan["policyId"]]
                budget.reserve(mid, "NATIVE_PARITY_CONTROL")
                print(canonical({"marketId": mid, "phase": "NATIVE_PARITY_CONTROL_STARTED",
                                 "budgetPoolReservedBE": budget.used}), flush=True)
                plain = cls(tape, slots, True, False, False)
                try:
                    native_result = plain.run_ladder("__UNSCORED__")
                    native_hash = digest(behavior_state(plain, native_result))
                finally:
                    plain.close()
                budget.reserve(mid, "SAME_POLICY_WITH_OBSERVER")
                print(canonical({"marketId": mid, "phase": "OBSERVED_BRANCH_STARTED",
                                 "budgetPoolReservedBE": budget.used}), flush=True)
                sink = TraceSink(output / str(mid))
                try:
                    sim = decorated(tape, slots, sink)
                    try:
                        native = sim.run_ladder("__UNSCORED__")
                        audited_hash = digest(behavior_state(sim, native))
                        row = sim.research_ledger.summary(int(sim.meta["lastReceivedMs"]))
                        row["openOrderCountAtHorizon"] = len(sim.slot_key)
                        row["cashAndReservationsPeak"] = str(sim.research_cash_reservation_peak)
                        reconcile(row, native)
                        if native_hash != audited_hash:
                            raise ValueError("observer changed native policy behavior")
                        write_json_new(sink.path / "native_state.json", behavior_state(sim, native))
                    finally:
                        sim.close()
                finally:
                    sink.close()
                row.update(marketId=mid, behaviorHash=audited_hash, parityVerified=True,
                           submits=native["submits"], traceManifest=sink.manifest())
                rows.append(row)
                print(canonical({"marketId": mid, "phase": "PARITY_PASS_TRACE_SAVED"}), flush=True)
            # Settlement is intentionally read only after ALL policy decisions finish.
            outcomes = read_json(plan["settlements"]["path"])
            if set(outcomes) != {str(m) for m in plan["marketIds"]}:
                raise ValueError("settlements must cover the exact frozen cohort")
            for row in rows:
                if outcomes[str(row["marketId"])] not in ("UP", "DOWN"):
                    raise ValueError("invalid settlement")
                row["winnerPostHocOnly"] = outcomes[str(row["marketId"])]
            result = {"schema": "MVPS_RESEARCH_RUN_V1", "researchOnly": True,
                      "liveAuthority": False, "evidenceKind": "REALISTIC_HFT_REPLAY",
                      "cohortKind": plan["cohortKind"], "policyId": plan["policyId"],
                      "marketIds": plan["marketIds"], "markets": rows,
                      "behaviorParityVerified": True, "planSha256": digest(plan),
                      "evaluationContract": plan["evaluationContract"],
                      "activityControl": plan.get("activityControl"),
                      "budgetPoolReservedBE": budget.used, "oldITTBudgetUnchanged": "8/10"}
            write_json_new(output / "result.json", result)
            write_json_new(output / "COMPLETED.json", {"resultSha256": file_digest(output / "result.json")})
            return result
    except BaseException as exc:
        write_json_new(output / "FAILED.json", {"status": "FAILED_NO_RETRY", "errorType": type(exc).__name__,
                                                "message": str(exc), "reservedBEAreNotRefunded": True})
        raise
