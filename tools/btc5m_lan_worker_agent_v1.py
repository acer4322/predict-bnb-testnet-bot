from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

VERSION = "BTC5M_LAN_WORKER_AGENT_V1"
ROOT = Path(os.environ.get("BTC5M_WORKER_ROOT", r"C:\BTC5M-worker")).resolve()
STATE_ROOT = ROOT / ".lan_worker_v1"
JOBS_ROOT = STATE_ROOT / "jobs"
RESULTS_ROOT = STATE_ROOT / "results"
STAGING_ROOT = STATE_ROOT / "staging"

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


def _filetime_to_int(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def cpu_percent(sample_seconds: float = 0.2) -> float:
    kernel32 = ctypes.windll.kernel32

    def snap() -> tuple[int, int, int]:
        idle = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return 0, 0, 0
        return _filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user)

    a = snap()
    time.sleep(sample_seconds)
    b = snap()
    idle = b[0] - a[0]
    total = (b[1] - a[1]) + (b[2] - a[2])
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - idle / total)))


def memory_info() -> dict[str, float]:
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    gb = 1024 ** 3
    return {
        "total_gb": round(status.ullTotalPhys / gb, 2),
        "free_gb": round(status.ullAvailPhys / gb, 2),
        "used_pct": round(float(status.dwMemoryLoad), 1),
    }


def process_running(image_name: str) -> bool:
    cp = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    text = cp.stdout.strip().lower()
    return bool(text and "no tasks are running" not in text and image_name.lower() in text)


def gpu_info() -> dict[str, Any]:
    try:
        cp = subprocess.run([
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,utilization.gpu,temperature.gpu,driver_version",
            "--format=csv,noheader,nounits",
        ], capture_output=True, text=True, timeout=10)
        if cp.returncode != 0:
            return {"available": False, "error": cp.stderr.strip() or cp.stdout.strip()}
        rows = []
        for i, line in enumerate(x.strip() for x in cp.stdout.splitlines() if x.strip()):
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 6:
                rows.append({
                    "index": i,
                    "name": parts[0],
                    "memory_total_mib": int(parts[1]),
                    "memory_free_mib": int(parts[2]),
                    "utilization_gpu_pct": int(parts[3]),
                    "temperature_c": int(parts[4]),
                    "driver_version": parts[5],
                })
        return {"available": bool(rows), "gpus": rows}
    except Exception as exc:
        return {"available": False, "error": repr(exc)}


def probe() -> dict[str, Any]:
    mem = memory_info()
    disk = shutil.disk_usage(ROOT.drive + "\\")
    return {
        "version": VERSION,
        "root": str(ROOT),
        "hostname": os.environ.get("COMPUTERNAME"),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "cpu_pct": round(cpu_percent(), 1),
        "memory": mem,
        "disk_free_gb": round(disk.free / (1024 ** 3), 1),
        "robocopy_active": process_running("robocopy.exe"),
        "gpu": gpu_info(),
        "jobs_root": str(JOBS_ROOT),
        "results_root": str(RESULTS_ROOT),
        "staging_root": str(STAGING_ROOT),
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def job_dir(job_id: str) -> Path:
    return JOBS_ROOT / job_id


def decode_spec(text: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(text.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def resolve_cwd(value: str | None) -> Path:
    p = ROOT if not value else (ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        p.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"cwd must stay under worker root: {p}") from exc
    if not p.exists() or not p.is_dir():
        raise ValueError(f"cwd does not exist: {p}")
    return p


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        raise ValueError("argv is empty")
    first = argv[0].lower()
    if first in {"python", "python.exe", "venv-python"}:
        argv = [str(ROOT / ".venv" / "Scripts" / "python.exe"), *argv[1:]]
    return argv


def set_child_limits(proc: subprocess.Popen[Any], max_threads: int) -> None:
    if os.name != "nt":
        return
    handle = ctypes.c_void_p(int(proc._handle))  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32
    kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
    logical = max(1, os.cpu_count() or 1)
    n = max(1, min(int(max_threads), logical, 63))
    mask = (1 << n) - 1
    kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t(mask))


def submit(spec: dict[str, Any]) -> dict[str, Any]:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    p = probe()
    max_start_cpu = float(spec.get("max_start_cpu_pct", 80.0))
    min_free_ram_gb = float(spec.get("min_free_ram_gb", 8.0))
    if p["cpu_pct"] > max_start_cpu:
        return {"accepted": False, "reason": "cpu_busy", "probe": p}
    if p["memory"]["free_gb"] < min_free_ram_gb:
        return {"accepted": False, "reason": "low_free_ram", "probe": p}
    if bool(spec.get("defer_if_copy_active", True)) and p.get("robocopy_active"):
        return {"accepted": False, "reason": "copy_active", "probe": p}
    resource_class = str(spec.get("resource_class", "cpu")).lower()
    if resource_class == "gpu":
        gpu_index = int(spec.get("gpu_index", 0))
        gp = p.get("gpu") or {}
        rows = gp.get("gpus") or []
        if not gp.get("available") or gpu_index < 0 or gpu_index >= len(rows):
            return {"accepted": False, "reason": "gpu_unavailable", "probe": p}
        row = rows[gpu_index]
        min_free_vram_mib = int(spec.get("min_free_vram_mib", 4096))
        max_start_gpu_pct = float(spec.get("max_start_gpu_pct", 70.0))
        if int(row.get("memory_free_mib", 0)) < min_free_vram_mib:
            return {"accepted": False, "reason": "low_free_vram", "probe": p}
        if float(row.get("utilization_gpu_pct", 100)) > max_start_gpu_pct:
            return {"accepted": False, "reason": "gpu_busy", "probe": p}

    job_id = str(spec.get("job_id") or f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in job_id):
        raise ValueError("unsafe job_id")
    jd = job_dir(job_id)
    if jd.exists():
        raise ValueError(f"job already exists: {job_id}")
    jd.mkdir(parents=True)

    spec = dict(spec)
    spec["job_id"] = job_id
    if bool(spec.get("cancel_protected")):
        spec["cancel_token"] = str(spec.get("cancel_token") or uuid.uuid4().hex)
    spec["cwd"] = str(resolve_cwd(spec.get("cwd")))
    spec["argv"] = normalize_argv(list(spec["argv"]))
    spec.setdefault("max_threads", 12)
    spec.setdefault("min_free_ram_gb", 8.0)
    spec.setdefault("max_start_cpu_pct", 80.0)
    spec.setdefault("submitted_at", time.time())
    atomic_json(jd / "spec.json", spec)
    atomic_json(jd / "status.json", {"job_id": job_id, "state": "queued", "submitted_at": spec["submitted_at"]})

    runner_cmd = subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve()), "_run", job_id])
    escaped = runner_cmd.replace("'", "''")
    ps = (
        "$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='{escaped}'}}; "
        "if ($r.ReturnValue -ne 0) { exit $r.ReturnValue }; "
        "Write-Output $r.ProcessId"
    )
    launch = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
        cwd=str(ROOT), capture_output=True, text=True, timeout=15,
    )
    if launch.returncode != 0:
        atomic_json(jd / "status.json", {
            "job_id": job_id, "state": "launch_failed",
            "submitted_at": spec["submitted_at"],
            "error": launch.stderr.strip() or launch.stdout.strip(),
        })
        return {"accepted": False, "job_id": job_id, "reason": "launch_failed", "error": launch.stderr.strip() or launch.stdout.strip(), "probe": p}
    launcher_pid = launch.stdout.strip().splitlines()[-1] if launch.stdout.strip() else None
    result={"accepted": True, "job_id": job_id, "launcher_pid": launcher_pid, "probe": p, "cancel_protected": bool(spec.get("cancel_protected"))}
    if spec.get("cancel_protected"): result["cancel_token"] = spec.get("cancel_token")
    return result



class ResearchAwake:
    """Keep this runner's system awake only while its research child exists.
    No display/away-mode request and no permanent power-plan mutation.
    Explicit user sleep/shutdown is not prevented.
    """
    def __init__(self, api=None):
        self.api = api
        self.previous = None
        self.active = False
        self.release_error = None

    def acquire(self):
        if self.api is None:
            self.api = ctypes.windll.kernel32.SetThreadExecutionState
            self.api.argtypes = [ctypes.c_uint32]
            self.api.restype = ctypes.c_uint32
        previous = int(self.api(0x80000001))
        if not previous:
            raise OSError("Unable to acquire temporary research sleep guard")
        self.previous = previous
        self.active = True

    def release(self):
        if self.active:
            restored = int(self.api(int(self.previous) | 0x80000000))
            if not restored:
                self.release_error = "SetThreadExecutionState release failed"
            self.active = False


def research_heartbeat(jd, result_dir, proc, awake, started, sample_number):
    """Runner health and researcher progress are separate evidence fields."""
    now = time.time()
    row = {"job_id": jd.name, "observed_at": now, "runner_pid": os.getpid(),
           "child_pid": proc.pid, "child_return_code": proc.poll(),
           "elapsed_wall_seconds": round(now-started, 3), "sample_number": sample_number,
           "sleep_guard_active": awake.active, "sleep_guard_release_error": awake.release_error,
           "stdout_bytes": (jd/"stdout.log").stat().st_size,
           "stderr_bytes": (jd/"stderr.log").stat().st_size}
    progress = result_dir/"progress.json"
    if progress.exists():
        row["progress_age_seconds"] = round(now-progress.stat().st_mtime, 3)
        if progress.stat().st_size <= 131072:
            try:
                data = load_json(progress)
                row["research_progress"] = {k:data[k] for k in ["phase","planned","complete","before","completed_before","native_new","errors"] if k in data}
            except (OSError, ValueError) as error:
                row["progress_read_error"] = repr(error)
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        children = [parent]+parent.children(recursive=True)
        samples = []
        for item in children:
            try:
                samples.append({"pid":item.pid,"created":item.create_time(),
                                "cpu_seconds":sum(item.cpu_times()[:2]),
                                "rss_bytes":item.memory_info().rss})
            except psutil.NoSuchProcess:
                pass
        row["process_samples"] = samples
    except Exception as error:
        row["process_sample_error"] = repr(error)
    atomic_json(jd/"heartbeat.json", row)


def wait_with_heartbeat(proc, jd, result_dir, awake, started):
    sample = 0
    while True:
        sample += 1
        try:
            research_heartbeat(jd, result_dir, proc, awake, started, sample)
        except Exception as error:
            # Monitoring failure must neither resubmit nor terminate research.
            with (jd/"runner_monitor_errors.log").open("a",encoding="utf-8") as stream:
                stream.write(repr(error)+"\n")
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            continue


def attach_heartbeat(payload, jd):
    path = jd/"heartbeat.json"
    if path.exists():
        try:
            heartbeat = load_json(path)
            heartbeat["age_seconds"] = round(time.time()-float(heartbeat["observed_at"]),3)
            heartbeat["stale_while_running"] = payload.get("state")=="running" and heartbeat["age_seconds"]>45
            payload["heartbeat"] = heartbeat
        except (OSError, ValueError, KeyError) as error:
            payload["heartbeat_error"] = repr(error)
    return payload


def run_job(job_id: str) -> int:
    jd = job_dir(job_id)
    spec = load_json(jd / "spec.json")
    argv = list(spec["argv"])
    cwd = str(spec["cwd"])
    max_threads = int(spec.get("max_threads", 12))
    env = os.environ.copy()
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env[key] = str(max_threads)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["BTC5M_LAN_WORKER_JOB_ID"] = job_id
    result_dir = RESULTS_ROOT / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    env["BTC5M_LAN_RESULT_DIR"] = str(result_dir)
    resource_class = str(spec.get("resource_class", "cpu")).lower()
    env["BTC5M_LAN_RESOURCE_CLASS"] = resource_class
    if resource_class == "gpu":
        env["CUDA_VISIBLE_DEVICES"] = str(spec.get("gpu_index", 0))

    stdout_path = jd / "stdout.log"
    stderr_path = jd / "stderr.log"
    started = time.time()
    atomic_json(jd / "status.json", {"job_id": job_id, "state": "starting", "started_at": started})
    awake = ResearchAwake()
    try:
        awake.acquire()
        with stdout_path.open("ab", buffering=0) as out, stderr_path.open("ab", buffering=0) as err:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            set_child_limits(proc, max_threads)
            atomic_json(jd / "status.json", {
                "job_id": job_id,
                "state": "running",
                "pid": proc.pid,
                "started_at": started,
                "argv": argv,
                "cwd": cwd,
                "max_threads": max_threads,
                "resource_class": resource_class,
                "result_dir": str(result_dir),
            })
            rc = wait_with_heartbeat(proc, jd, result_dir, awake, started)
            awake.release()
            research_heartbeat(jd, result_dir, proc, awake, started, -1)
        ended = time.time()
        atomic_json(jd / "status.json", {
            "job_id": job_id,
            "state": "succeeded" if rc == 0 else "failed",
            "pid": proc.pid,
            "return_code": rc,
            "started_at": started,
            "ended_at": ended,
            "elapsed_seconds": round(ended - started, 3),
            "resource_class": resource_class,
            "result_dir": str(result_dir),
        })
        return int(rc)
    except Exception as exc:
        atomic_json(jd / "status.json", {
            "job_id": job_id,
            "state": "runner_error",
            "started_at": started,
            "ended_at": time.time(),
            "error": repr(exc),
            "resource_class": resource_class if "resource_class" in locals() else "unknown",
            "result_dir": str(result_dir) if "result_dir" in locals() else None,
        })
        return 99
    finally:
        awake.release()


def status(job_id: str | None = None) -> Any:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    if job_id:
        path = job_dir(job_id) / "status.json"
        if not path.exists():
            return {"job_id": job_id, "state": "missing"}
        payload = load_json(path)
        jd = job_dir(job_id)
        payload["stdout_bytes"] = (jd / "stdout.log").stat().st_size if (jd / "stdout.log").exists() else 0
        payload["stderr_bytes"] = (jd / "stderr.log").stat().st_size if (jd / "stderr.log").exists() else 0
        return attach_heartbeat(payload, jd)
    rows = []
    for d in sorted(JOBS_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if JOBS_ROOT.exists() else []:
        if not d.is_dir() or not (d / "status.json").exists():
            continue
        rows.append(load_json(d / "status.json"))
    return rows[:50]


def tail(job_id: str, which: str, max_bytes: int) -> dict[str, Any]:
    path = job_dir(job_id) / ("stderr.log" if which == "stderr" else "stdout.log")
    if not path.exists():
        return {"job_id": job_id, "stream": which, "text": "", "bytes": 0}
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        raw = fh.read()
    return {"job_id": job_id, "stream": which, "text": raw.decode("utf-8", errors="replace"), "bytes": size}


def _append_cancel_audit(payload: dict[str, Any]) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    path = STATE_ROOT / "cancel_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def cancel(job_id: str, token: str | None = None, force: bool = False) -> dict[str, Any]:
    current = status(job_id)
    spec_path = job_dir(job_id) / "spec.json"
    spec = load_json(spec_path) if spec_path.exists() else {}
    protected = bool(spec.get("cancel_protected"))
    expected = str(spec.get("cancel_token") or "")
    audit = {
        "requested_at": time.time(), "job_id": job_id, "protected": protected,
        "token_supplied": bool(token), "force": bool(force),
        "request_host": os.environ.get("COMPUTERNAME"), "request_user": os.environ.get("USERNAME"),
        "state_before": current.get("state") if isinstance(current, dict) else None,
    }
    if protected and not force and (not token or token != expected):
        audit.update({"accepted": False, "reason": "protected_cancel_token_required"}); _append_cancel_audit(audit)
        return {"ok": False, "job_id": job_id, "reason": "protected_cancel_token_required", "protected": True, "status": current}
    pid = current.get("pid") if isinstance(current, dict) else None
    if not pid:
        audit.update({"accepted": False, "reason": "no_pid"}); _append_cancel_audit(audit)
        return {"ok": False, "job_id": job_id, "reason": "no_pid", "status": current}
    cp = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    atomic_json(job_dir(job_id) / "status.json", {
        "job_id": job_id, "state": "cancelled", "pid": pid, "ended_at": time.time(),
        "taskkill_return_code": cp.returncode, "cancel_protected": protected,
    })
    audit.update({"accepted": cp.returncode == 0, "taskkill_return_code": cp.returncode}); _append_cancel_audit(audit)
    return {"ok": cp.returncode == 0, "job_id": job_id, "taskkill_return_code": cp.returncode, "protected": protected}


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    s = sub.add_parser("submit")
    s.add_argument("spec_b64")
    r = sub.add_parser("_run")
    r.add_argument("job_id")
    st = sub.add_parser("status")
    st.add_argument("job_id", nargs="?")
    t = sub.add_parser("tail")
    t.add_argument("job_id")
    t.add_argument("--stream", choices=["stdout", "stderr"], default="stdout")
    t.add_argument("--max-bytes", type=int, default=16384)
    c = sub.add_parser("cancel")
    c.add_argument("job_id"); c.add_argument("--token"); c.add_argument("--force", action="store_true")
    ns = ap.parse_args()

    if ns.cmd == "probe":
        emit(probe())
        return 0
    if ns.cmd == "submit":
        emit(submit(decode_spec(ns.spec_b64)))
        return 0
    if ns.cmd == "_run":
        return run_job(ns.job_id)
    if ns.cmd == "status":
        emit(status(ns.job_id))
        return 0
    if ns.cmd == "tail":
        emit(tail(ns.job_id, ns.stream, ns.max_bytes))
        return 0
    if ns.cmd == "cancel":
        emit(cancel(ns.job_id, ns.token, ns.force))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
