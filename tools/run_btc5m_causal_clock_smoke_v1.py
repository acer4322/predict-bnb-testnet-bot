"""Single-market native clock ablation; run from a frozen staged package."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ORIGINAL = "producer.total_frames=max(1,len(sim.payload['updates']))"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(source, old, new):
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


def transformed(package, manifest, mode):
    source = (package / "frozen_runner.py").read_text(encoding="utf-8")
    source = replace_once(source,
        "HELPER_PATH=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/run_target_core_cycle_rearm_v3.py')",
        f"HELPER_PATH=Path({str(package / 'helper_rearm.py')!r})")
    source = replace_once(source,
        "ADAPTER_STAGED=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_native_active_adapter_v1.py')",
        f"ADAPTER_STAGED=Path({str(package / 'adapter.py')!r})")
    # No recursive deletion, even on retry: every native arm requires a fresh job id.
    source = replace_once(source, "if root.exists():shutil.rmtree(root)",
                          "if root.exists():raise RuntimeError('CLOCK_SMOKE_WORKDIR_ALREADY_EXISTS')")
    source = replace_once(source, "self.total_frames=1", "self.total_frames=1;self.clock_observations=[]")
    loop = next(line for line in source.splitlines() if line.strip() == "for s in pid:")
    indent = loop[:len(loop) - len(loop.lstrip())]
    source = replace_once(source, loop + "\n",
        indent + "self.clock_observations.append(dict(t=int(f['t']),index=int(f['index']),p=p,gross=gross,desired=dict(desired)))\n" + loop + "\n")
    source = replace_once(source, "  result['safety_gate']=dict(",
        "  result['clock_smoke']=_save_clock_trace(out,tr,producer,len(sim.payload['updates']))\n  result['safety_gate']=dict(")
    if mode == "FIXED_TRAIN_EVENTS":
        source = replace_once(source, ORIGINAL, f"producer.total_frames={manifest['fixed_train_total_frames']!r}")
    else:
        assert mode == "LEGACY_REPLAY_EVENTS" and source.count(ORIGINAL) == 1
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clock-mode", choices=("LEGACY_REPLAY_EVENTS", "FIXED_TRAIN_EVENTS"), required=True)
    parser.add_argument("--check-only", action="store_true")
    args, native_args = parser.parse_known_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        assert digest(package / name) == expected, name
    legacy = transformed(package, manifest, "LEGACY_REPLAY_EVENTS")
    causal = transformed(package, manifest, "FIXED_TRAIN_EVENTS")
    assert causal == replace_once(legacy, ORIGINAL, f"producer.total_frames={manifest['fixed_train_total_frames']!r}")
    for source in (legacy, causal):
        compile(source, str(package / "frozen_runner.py"), "exec")
    if args.check_only:
        print(json.dumps(dict(status="PASS", only_arm_difference="total_frames assignment",
                              fixed_train_total_frames=manifest["fixed_train_total_frames"])))
        return
    assert native_args == ["--market", "2026085", "--route-mode", "PASSIVE_ONLY", "--rearm-mode", "RESPONSIBILITY_DUAL_CAPACITY"]
    namespace = {"__name__": "clock_smoke_frozen", "__file__": str(package / "frozen_runner.py")}
    def save_trace(out, trace, producer, actual_frames):
        payload = dict(states=trace.states, plans=trace.clock_plans, native_actions=trace.actions,
                       observations=producer.clock_observations)
        path = out / "clock_trace.json.gz"
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        with gzip.GzipFile(filename=str(path), mode="wb", mtime=0) as f:
            f.write(raw)
        return dict(mode=args.clock_mode, runtime_clock_total_frames=producer.total_frames,
            actual_replay_frames=actual_frames, fixed_train_total_frames=manifest["fixed_train_total_frames"],
            trace_payload_sha256=hashlib.sha256(raw).hexdigest(), trace_file=path.name,
            manifest_sha256=digest(package / "manifest.json"), wrapper_sha256=digest(Path(__file__)),
            frozen_runner_sha256=manifest["files"]["frozen_runner.py"],
            transformed_source_sha256=hashlib.sha256((legacy if args.clock_mode == "LEGACY_REPLAY_EVENTS" else causal).encode()).hexdigest(),
            observations=len(producer.clock_observations), plans=len(trace.clock_plans),
            native_actions=len(trace.actions), states=len(trace.states), research_only=True)
    namespace["_save_clock_trace"] = save_trace
    exec(legacy if args.clock_mode == "LEGACY_REPLAY_EVENTS" else causal, namespace)
    original_loader = namespace["load_module"]
    def instrumented_loader(name, path):
        module = original_loader(name, path)
        if path == namespace["HELPER_PATH"]:
            class ClockTrace(module.TraceLite):
                def __init__(self):
                    super().__init__()
                    self.clock_plans = []
                def plan(self, event):
                    super().plan(event)
                    self.clock_plans.append(dict(t=self.now, operations=event["operations"]))
            module.TraceLite = ClockTrace
        return module
    namespace["load_module"] = instrumented_loader
    sys.argv = [str(package / "frozen_runner.py"), *native_args]
    namespace["main"]()


if __name__ == "__main__":
    main()
