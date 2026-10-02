"""Dependency-free, persistent ask/tell search.

Method: seeded global exploration plus Pareto-neighborhood mutation.
This is not Optuna, TPE, Bayesian optimization, or neural-network training.
The adapter supplies measurements; this module knows nothing about trading.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Any

VERSION = "BTC5M_BATCH_SEARCH_V1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def atomic_bytes(path: Path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, canonical(value))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class WriterLock:
    """OS lock: automatically released when a worker exits or is killed."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.stream.seek(0, 2) == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Another process owns this study") from exc
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None


def dominates(a: list[float], b: list[float]) -> bool:
    if not a or len(a) != len(b):
        raise ValueError("Objective dimensions differ or are empty")
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def pareto(rows: list[dict]) -> list[dict]:
    good = [r for r in rows if r["state"] == "COMPLETE"]
    return [a for a in good if not any(
        dominates(b["objectives"], a["objectives"])
        for b in good if a["number"] != b["number"])]


def validate_spec(spec: dict) -> None:
    if not isinstance(spec["trials"], int) or isinstance(spec["trials"], bool) or spec["trials"] < 1:
        raise ValueError("Positive integer trial budget required")
    if not spec["objectives"] or not spec["space"]:
        raise ValueError("Empty objective list or search space")
    if not 0 < spec.get("exploration_probability", .35) <= 1:
        raise ValueError("Exploration probability must be in (0,1]")
    for key, field in spec["space"].items():
        kind = field["type"]
        if kind == "choice":
            if not field["values"] or len({digest(v) for v in field["values"]}) != len(field["values"]):
                raise ValueError("Empty/duplicate choices: " + key)
        elif kind in ("float", "log_float"):
            low, high = field["low"], field["high"]
            if not (math.isfinite(low) and math.isfinite(high) and low < high):
                raise ValueError("Invalid bounds: " + key)
            if kind == "log_float" and low <= 0:
                raise ValueError("Log bounds must be positive")
        else:
            raise ValueError("Unsupported range: " + kind)


def validate_parameters(space: dict, params: dict) -> None:
    if set(space) != set(params):
        raise ValueError("Parameter names differ from the frozen space")
    for key, field in space.items():
        value = params[key]
        if field["type"] == "choice":
            if value not in field["values"]:
                raise ValueError("Invalid choice: " + key)
        elif (not isinstance(value, (int, float)) or isinstance(value, bool)
              or not math.isfinite(value) or not field["low"] <= value <= field["high"]):
            raise ValueError("Out-of-range parameter: " + key)


class Study:
    """One writer. Incomplete native scenarios are not silently retried.

    Explicit recovery reopens a RUNNING *trial*. The adapter must reconcile its
    recorded native scenarios, and refuse unknown/failed scenario work until a
    separate, explicitly authorized recovery has been recorded.
    """
    def __init__(self, path: Path, spec: dict, recover_interrupted: bool = False):
        validate_spec(spec)
        self.path, self.spec = Path(path), spec
        if self.path.exists():
            self.data = read_json(self.path)
            if self.data["spec_hash"] != digest(spec) or self.data["version"] != VERSION:
                raise ValueError("Frozen spec/version mismatch; create a successor")
            for row in self.data["trials"]:
                validate_parameters(spec["space"], row["params"])
                if row["parameter_hash"] != digest(row["params"]):
                    raise ValueError("Saved parameter hash mismatch")
                if row["state"] == "RUNNING":
                    if not recover_interrupted:
                        raise RuntimeError("RUNNING trial requires explicit recovery")
                    row["state"] = "PENDING"
                    row.setdefault("recoveries", []).append({
                        "reason": "EXPLICIT_INTERRUPTED_RECOVERY", "previous_attempts": row["attempts"]})
            self.save()
        else:
            self.data = {"version": VERSION, "spec_hash": digest(spec), "spec": spec, "trials": []}
            self.save()

    @property
    def trials(self) -> list[dict]:
        return self.data["trials"]

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def ask(self) -> dict:
        if any(r["state"] == "RUNNING" for r in self.trials):
            raise RuntimeError("Only one in-flight trial is allowed")
        pending = [r for r in self.trials if r["state"] == "PENDING"]
        if pending:
            return pending[0]
        number = len(self.trials)
        if number >= self.spec["trials"]:
            raise StopIteration
        rng = random.Random(int(digest([self.spec["seed"], number])[:16], 16))
        front = pareto(self.trials)
        used = {digest(r["params"]) for r in self.trials}
        startup = self.spec.get("startup", 6)
        for attempt in range(1024):
            adaptive = (number >= startup and bool(front)
                        and (number == startup or rng.random() >= self.spec.get("exploration_probability", .35)))
            parent = rng.choice(front) if adaptive else None
            params, radix = {}, 1
            for key, field in sorted(self.spec["space"].items()):
                if field["type"] == "choice":
                    if adaptive and rng.random() < .7:
                        value = parent["params"][key]
                    elif number < startup and attempt == 0:
                        value = field["values"][(number // radix) % len(field["values"])]
                    else:
                        value = rng.choice(field["values"])
                    radix *= len(field["values"])
                else:
                    log = field["type"] == "log_float"
                    low = math.log(field["low"]) if log else field["low"]
                    high = math.log(field["high"]) if log else field["high"]
                    if adaptive:
                        center = math.log(parent["params"][key]) if log else parent["params"][key]
                        value = min(high, max(low, rng.gauss(center, .18 * (high - low))))
                    else:
                        value = rng.uniform(low, high)
                    value = math.exp(value) if log else value
                    value = max(field["low"], min(field["high"], value))
                params[key] = value
            validate_parameters(self.spec["space"], params)
            if digest(params) not in used:
                break
        else:
            raise RuntimeError("No unique point found in the declared space")
        row = {"number": number, "params": params, "parameter_hash": digest(params),
               "state": "PENDING", "attempts": 0,
               "sampling": "PARETO_NEIGHBOR" if adaptive else "GLOBAL_EXPLORATION",
               "parent_trial": parent["number"] if parent else None,
               "completed_results_used": [r["number"] for r in self.trials if r["state"] == "COMPLETE"]}
        self.trials.append(row)
        self.save()  # Persist proposal before allowing any execution.
        return row

    def begin(self, number: int) -> None:
        row = self.trials[number]
        if row["state"] != "PENDING":
            raise RuntimeError("Trial is not pending")
        row.update(state="RUNNING", attempts=row["attempts"] + 1)
        self.save()

    def tell(self, number: int, objectives: list[float], metrics: dict) -> None:
        row = self.trials[number]
        if row["state"] != "RUNNING":
            raise RuntimeError("Only RUNNING trials accept results")
        if len(objectives) != len(self.spec["objectives"]) or not all(math.isfinite(v) for v in objectives):
            raise ValueError("Invalid objective vector")
        # Validate before mutating in-memory state, including metric NaN checks.
        canonical(metrics)
        row.update(state="COMPLETE", objectives=list(objectives), metrics=metrics)
        self.save()

    def fail(self, number: int, reason: str) -> None:
        if self.trials[number]["state"] != "RUNNING":
            raise RuntimeError("Trial is not running")
        self.trials[number].update(state="FAILED", error=reason)
        self.save()

    def shortlist(self, count: int) -> list[int]:
        front, selected = pareto(self.trials), []
        for objective in range(len(self.spec["objectives"])):
            if not front or len(selected) >= count:
                break
            row = max(front, key=lambda r: (r["objectives"][objective], -r["number"]))
            if row["number"] not in selected:
                selected.append(row["number"])
        for row in sorted(front, key=lambda r: (r["objectives"], -r["number"]), reverse=True):
            if len(selected) >= count:
                break
            if row["number"] not in selected:
                selected.append(row["number"])
        return selected
