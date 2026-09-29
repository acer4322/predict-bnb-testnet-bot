"""Frozen causal-clock Repair route x Fresh admission exact factorial.

Reuses the native clock wrapper and V39 event-based deferral semantics. Only one
preselected checkpoint is intervened on; afterwards the frozen manager resumes.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instrument(source, replace_once):
    source = replace_once(source, "self.clock_observations=[]", "self.clock_observations=[];self.mechanism=_Mechanism()")
    line = next(x for x in source.splitlines() if x.strip() == "if f['t']>=f['end']:")
    pad = line[:len(line) - len(line.lstrip())]
    source = replace_once(source, line + "\n", pad + "self.mechanism.on_frame(f,atomic_transition,terminal_changed)\n" + line + "\n")
    line = next(x for x in source.splitlines() if x.strip().startswith("exact_match=(a.exact_frontier_time"))
    pad = line[:len(line) - len(line.lstrip())]
    source = replace_once(source, line + "\n", pad + "if self.mechanism.fresh_gate(f,self,ss,kind,qty,price,repair_need):continue\n" + line + "\n")
    return source


def mechanism_class(selection, admission):
    class Mechanism:
        def __init__(self):
            self.pending = False
            self.hits = []
            self.events = []
            self.suppressed = 0

        def on_frame(self, f, atomic_transition, terminal_changed):
            if self.pending and f["t"] > selection["t"] and (atomic_transition or terminal_changed):
                self.pending = False
                self.events.append(dict(event="DEFER_RELEASE", t=int(f["t"]),
                    atomic_transition=bool(atomic_transition), terminal_changed=bool(terminal_changed)))

        def fresh_gate(self, f, producer, side, kind, qty, price, repair_need):
            selected = int(f["t"]) == selection["t"] and side == selection["fresh"]["side"] and kind == "FRESH"
            if selected:
                assert not self.hits
                assert abs(qty - selection["fresh"]["qty"]) < 1e-9
                assert abs(price - selection["fresh"]["price"]) < 1e-9
                assert repair_need[side] == 0, "Fresh intervention must be BIRTH_ONLY at this state"
                self.hits.append(dict(t=int(f["t"]), index=int(f["index"]), side=side, qty=qty, price=price,
                    gateway_state_id=f["gateway_state_id"], inventory=dict(f["own_view"]["inv"]),
                    cost=float(f["own_view"]["cost"]), atomic_queues=json.loads(json.dumps(producer.atomic.q))))
                if admission == "DEFER_NEXT_WAKE":
                    self.pending = True
                    self.events.append(dict(event="DEFER_START", t=int(f["t"]), side=side, qty=qty))
                    self.suppressed += 1
                    return True
            if self.pending and side == selection["fresh"]["side"] and kind == "FRESH":
                self.suppressed += 1
                return True
            return False
    return Mechanism


def self_test():
    selection = dict(t=100, fresh=dict(side="DOWN", qty=20., price=.2))
    from types import SimpleNamespace
    p = SimpleNamespace(atomic=SimpleNamespace(q={"UP": [], "DOWN": []}))
    f = dict(t=100, index=1, gateway_state_id="x", own_view=dict(inv=dict(UP=1., DOWN=2.), cost=1.))
    for admission in ("ADMIT_NOW", "DEFER_NEXT_WAKE"):
        m = mechanism_class(selection, admission)()
        assert m.fresh_gate(f, p, "DOWN", "FRESH", 20., .2, {"DOWN": 0}) == (admission == "DEFER_NEXT_WAKE")
        m.on_frame(f, True, True)
        if admission == "DEFER_NEXT_WAKE":
            assert m.pending
            later = dict(f, t=101)
            m.on_frame(later, False, False)
            assert m.pending
            assert not m.fresh_gate(later, p, "UP", "REPAIR", 20., .8, {"UP": 20})
            m.on_frame(later, True, False)
            assert not m.pending
            assert not m.fresh_gate(later, p, "DOWN", "FRESH", 20., .2, {"DOWN": 0})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-route", choices=("PASSIVE", "ACTIVE"), required=True)
    parser.add_argument("--fresh-admission", choices=("ADMIT_NOW", "DEFER_NEXT_WAKE"), required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    self_test()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        assert sha(package / name) == expected, name
    base = load("frozen_clock_wrapper", package / "clock_wrapper.py")
    selection = manifest["selection"]
    source = instrument(base.transformed(package, manifest, "FIXED_TRAIN_EVENTS"), base.replace_once)
    compile(source, str(package / "frozen_runner.py"), "exec")
    if args.check_only:
        print(json.dumps(dict(status="PASS", checks="hashes, syntax, admit no-op, event-only defer release, repair unaffected",
                              selection=selection)))
        return
    namespace = {"__name__": "mechanism_factorial_frozen", "__file__": str(package / "frozen_runner.py"),
                 "_Mechanism": mechanism_class(selection, args.fresh_admission)}
    def save_trace(out, trace, producer, actual_frames):
        assert len(producer.mechanism.hits) == 1
        assert len(producer.frontier_exact_hits) == 1
        assert producer.total_frames == manifest["fixed_train_total_frames"]
        payload = dict(states=trace.states, plans=trace.clock_plans, native_actions=trace.actions,
                       observations=producer.clock_observations)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        with gzip.GzipFile(filename=str(out / "clock_trace.json.gz"), mode="wb", mtime=0) as f:
            f.write(raw)
        return dict(version="BTC5M_CORE_MECHANISM_FACTORIAL_V1", mode="FIXED_TRAIN_EVENTS",
            repair_route=args.repair_route, fresh_admission=args.fresh_admission,
            runtime_clock_total_frames=producer.total_frames, actual_replay_frames=actual_frames,
            fixed_train_total_frames=manifest["fixed_train_total_frames"],
            trace_payload_sha256=hashlib.sha256(raw).hexdigest(), trace_file="clock_trace.json.gz",
            manifest_sha256=sha(package / "manifest.json"), runner_sha256=sha(Path(__file__)),
            transformed_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            selection=selection, fresh_hits=producer.mechanism.hits, defer_events=producer.mechanism.events,
            deferred_candidate_count=producer.mechanism.suppressed, defer_pending_final=producer.mechanism.pending)
    namespace["_save_clock_trace"] = save_trace
    exec(source, namespace)
    original_loader = namespace["load_module"]
    def loader(name, path):
        module = original_loader(name, path)
        if path == namespace["HELPER_PATH"]:
            class Trace(module.TraceLite):
                def __init__(self):
                    super().__init__()
                    self.clock_plans = []
                def plan(self, event):
                    super().plan(event)
                    self.clock_plans.append(dict(t=self.now, operations=event["operations"]))
            module.TraceLite = Trace
        return module
    namespace["load_module"] = loader
    sys.argv = [str(package / "frozen_runner.py"), "--market", "2026085", "--route-mode", "PASSIVE_ONLY",
        "--rearm-mode", "RESPONSIBILITY_DUAL_CAPACITY", "--exact-frontier-time", str(selection["t"]),
        "--exact-frontier-side", selection["repair"]["side"], "--exact-frontier-kind", "REPAIR",
        "--exact-frontier-route", args.repair_route]
    namespace["main"]()


if __name__ == "__main__":
    main()
