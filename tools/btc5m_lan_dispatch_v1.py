from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

VERSION = "BTC5M_LAN_DISPATCH_V1"
DEFAULT_HOST = "btc5m-worker"
REMOTE_ROOT = r"C:\BTC5M-worker"
REMOTE_PY = REMOTE_ROOT + r"\.venv\Scripts\python.exe"
REMOTE_AGENT = REMOTE_ROOT + r"\tools\btc5m_lan_worker_agent_v1.py"
LOCAL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RETURN_ROOT = LOCAL_ROOT / "data" / "research" / "lan_worker_returns"
AUTO_COLLECT_POLL_SECONDS = 1.0
AUTO_COLLECT_MAX_SECONDS = 604800


def _background_run_kwargs() -> dict[str, int]:
    """Keep short-lived ssh/scp children from flashing a console window on Windows."""
    if os.name != "nt":
        return {}
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"creationflags": flags} if flags else {}


def remote(host: str, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    command = " ".join([REMOTE_PY, REMOTE_AGENT, *args])
    return subprocess.run(
        ["ssh", host, command], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
        **_background_run_kwargs(),
    )


def ssh_raw(host: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", host, command], capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
        **_background_run_kwargs(),
    )


def parse_json_output(cp: subprocess.CompletedProcess[str]) -> Any:
    if cp.returncode != 0:
        raise RuntimeError(f"remote rc={cp.returncode}: {cp.stderr.strip() or cp.stdout.strip()}")
    lines = [x.strip() for x in cp.stdout.splitlines() if x.strip()]
    if not lines:
        raise RuntimeError("remote returned no JSON")
    return json.loads(lines[-1])


def encode_spec(spec: dict[str, Any]) -> str:
    raw = json.dumps(spec, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def ensure_remote_transfer_dirs(host: str) -> None:
    code = (
        "from pathlib import Path; "
        "[Path(p).mkdir(parents=True,exist_ok=True) for p in "
        "[r'C:\\BTC5M-worker\\.lan_worker_v1\\staging',r'C:\\BTC5M-worker\\.lan_worker_v1\\results']]"
    )
    command = subprocess.list2cmdline([REMOTE_PY, "-c", code])
    cp = ssh_raw(host, command, timeout=20)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())


def cmd_probe(host: str) -> Any:
    return parse_json_output(remote(host, ["probe"]))


def cmd_gpu_probe(host: str) -> Any:
    command = (
        'nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu,temperature.gpu,'
        'driver_version --format=csv,noheader,nounits'
    )
    cp = ssh_raw(host, command, timeout=20)
    if cp.returncode != 0:
        return {"available": False, "error": cp.stderr.strip() or cp.stdout.strip()}
    rows = []
    for i, line in enumerate(x.strip() for x in cp.stdout.splitlines() if x.strip()):
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 6:
            rows.append({
                "index": i, "name": parts[0], "memory_total_mib": int(parts[1]),
                "memory_free_mib": int(parts[2]), "utilization_gpu_pct": int(parts[3]),
                "temperature_c": int(parts[4]), "driver_version": parts[5],
            })
    torch_code = (
        "import importlib.util,json; "
        "s=importlib.util.find_spec('torch'); "
        "print(json.dumps({'torch_installed':bool(s)}))"
    )
    tcp = ssh_raw(host, subprocess.list2cmdline([REMOTE_PY, "-c", torch_code]), timeout=20)
    torch_info = {"torch_installed": False}
    if tcp.returncode == 0 and tcp.stdout.strip():
        try:
            torch_info = json.loads(tcp.stdout.strip().splitlines()[-1])
        except Exception:
            pass
    return {"available": bool(rows), "gpus": rows, **torch_info}


def cmd_status(host: str, job_id: str | None) -> Any:
    return parse_json_output(remote(host, ["status"] + ([job_id] if job_id else [])))


def cmd_tail(host: str, job_id: str, stream: str, max_bytes: int) -> Any:
    return parse_json_output(remote(host, ["tail", job_id, "--stream", stream, "--max-bytes", str(max_bytes)]))


def cmd_cancel(host: str, job_id: str, token: str | None = None, force: bool = False) -> Any:
    args=["cancel", job_id]
    if token: args += ["--token", token]
    if force: args.append("--force")
    return parse_json_output(remote(host, args))


def cmd_stage(host: str, local_path: str) -> Any:
    source = Path(local_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    ensure_remote_transfer_dirs(host)
    remote_dir = "C:/BTC5M-worker/.lan_worker_v1/staging/"
    args = ["scp"]
    if source.is_dir():
        args.append("-r")
    args.extend([str(source), f"{host}:{remote_dir}"])
    cp = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **_background_run_kwargs())
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
    return {
        "ok": True, "source": str(source),
        "remote_relative": f".lan_worker_v1/staging/{source.name}",
        "remote_absolute": rf"C:\BTC5M-worker\.lan_worker_v1\staging\{source.name}",
    }


def _collect_root(local_root: str | None = None) -> Path:
    root = Path(local_root).expanduser().resolve() if local_root else DEFAULT_RETURN_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def notify_host_job_terminal(job_id: str, state: str, local_root: str | None = None, collected: bool = False) -> dict[str, Any]:
    """Best-effort Windows desktop notice; never changes research/job success semantics."""
    if os.name != "nt":
        return {"attempted": False, "reason": "non_windows_host"}
    if os.environ.get("BTC5M_HOST_NOTIFY", "1").strip().lower() in {"0", "false", "off", "no"}:
        return {"attempted": False, "reason": "disabled_by_BTC5M_HOST_NOTIFY"}
    try:
        import shutil
        powershell = shutil.which("powershell.exe")
        if not powershell:
            return {"attempted": False, "reason": "powershell_not_found"}
        success = state == "succeeded" and collected
        title = "BTC5M Worker"
        message = (f"{job_id}\n已完成並收回主機。" if success
                   else f"{job_id}\n工作狀態：{state}")
        icon = "Info" if success else "Warning"
        sound = "Asterisk" if success else "Exclamation"
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            "$n=New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$n.BalloonTipTitle={_powershell_quote(title)}; "
            f"$n.BalloonTipText={_powershell_quote(message)}; "
            f"$n.BalloonTipIcon=[System.Windows.Forms.ToolTipIcon]::{icon}; "
            "$n.Visible=$true; "
            f"[System.Media.SystemSounds]::{sound}.Play(); "
            "$n.ShowBalloonTip(8000); Start-Sleep -Seconds 10; $n.Dispose()"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        log_root = _collect_root(local_root) / "_auto_collect_logs"
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / f"{job_id}.notify.log"
        flags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with log_path.open("ab", buffering=0) as log:
            proc = subprocess.Popen(
                [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                cwd=str(LOCAL_ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                close_fds=True, creationflags=flags,
            )
        return {"attempted": True, "pid": proc.pid, "log": str(log_path), "state": state, "collected": bool(collected)}
    except Exception as ex:
        return {"attempted": False, "error": f"{type(ex).__name__}:{ex}"}


def _collect_marker(root: Path, job_id: str) -> Path:
    d = root / "_auto_collect_state"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.json"


def _write_collect_marker(root: Path, job_id: str, destination: Path, mode: str) -> None:
    marker = _collect_marker(root, job_id)
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "job_id": job_id, "collected_at": time.time(), "local_path": str(destination), "mode": mode
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, marker)


def _acquire_collect_lock(root: Path, job_id: str, wait_seconds: float = 30.0) -> Path:
    d = root / "_auto_collect_locks"
    d.mkdir(parents=True, exist_ok=True)
    lock = d / f"{job_id}.lock"
    deadline = time.time() + max(1.0, float(wait_seconds))
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
            return lock
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 600:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise TimeoutError(f"collect lock timeout for {job_id}")
            time.sleep(0.2)


def cmd_collect(host: str, job_id: str, local_root: str | None = None, mode: str = "manual") -> Any:
    ensure_remote_transfer_dirs(host)
    root = _collect_root(local_root)
    destination = root / job_id
    marker = _collect_marker(root, job_id)
    if destination.exists() and marker.exists():
        return {"ok": True, "job_id": job_id, "local_path": str(destination), "already_collected": True}
    lock = _acquire_collect_lock(root, job_id)
    try:
        if destination.exists() and marker.exists():
            return {"ok": True, "job_id": job_id, "local_path": str(destination), "already_collected": True}
        import shutil
        temp_parent = root / f"._collecting_{job_id}_{os.getpid()}_{int(time.time()*1000)}"
        temp_parent.mkdir(parents=True, exist_ok=False)
        try:
            source = f"{host}:C:/BTC5M-worker/.lan_worker_v1/results/{job_id}"
            cp = subprocess.run(["scp", "-r", source, str(temp_parent)], capture_output=True, text=True, encoding="utf-8", errors="replace", **_background_run_kwargs())
            if cp.returncode != 0:
                raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())
            staged = temp_parent / job_id
            if not staged.exists():
                raise RuntimeError(f"collected directory missing after scp: {staged}")
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(staged, destination)
            _write_collect_marker(root, job_id, destination, mode)
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)
        return {"ok": True, "job_id": job_id, "local_path": str(destination), "auto_collected": mode == "auto"}
    finally:
        lock.unlink(missing_ok=True)


def cmd_auto_collect_watch(host: str, job_id: str, local_root: str | None = None, poll_seconds: float = AUTO_COLLECT_POLL_SECONDS, max_seconds: int = AUTO_COLLECT_MAX_SECONDS) -> Any:
    terminal = {"succeeded", "failed", "runner_error", "cancelled", "launch_failed"}
    started = time.time()
    checks = 0
    last: Any = None
    last_state: str | None = None
    status_errors = 0
    collect_errors = 0
    while time.time() - started <= max(1, int(max_seconds)):
        checks += 1
        try:
            last = cmd_status(host, job_id)
        except Exception as ex:
            status_errors += 1
            last = {"job_id": job_id, "state": "status_error", "error": f"{type(ex).__name__}:{ex}"}
            if os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
                print(f"[BTC5M] status check failed ({status_errors}); retrying...", flush=True)
            time.sleep(max(0.5, float(poll_seconds)))
            continue
        state = last.get("state") if isinstance(last, dict) else None
        if state != last_state and os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
            print(f"[BTC5M] remote state: {state}", flush=True)
            last_state = state
        if state == "succeeded":
            try:
                collected = cmd_collect(host, job_id, local_root, mode="auto")
            except Exception as ex:
                collect_errors += 1
                if os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
                    print(f"[BTC5M] collection failed ({collect_errors}); retrying... {type(ex).__name__}", flush=True)
                time.sleep(max(1.0, float(poll_seconds)))
                continue
            notification = notify_host_job_terminal(job_id, state, local_root, collected=True)
            if os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
                print("[BTC5M] COMPLETE + COLLECTED. Closing this window.", flush=True)
            return {"ok": True, "job_id": job_id, "state": state, "checks": checks,
                    "status_errors": status_errors, "collect_errors": collect_errors,
                    "collected": collected, "notification": notification}
        if state in terminal:
            notification = notify_host_job_terminal(job_id, state, local_root, collected=False)
            if os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
                print(f"[BTC5M] TERMINAL STATE: {state}. Closing this window.", flush=True)
            return {"ok": False, "job_id": job_id, "state": state, "checks": checks,
                    "status_errors": status_errors, "collect_errors": collect_errors,
                    "status": last, "notification": notification}
        time.sleep(max(0.2, float(poll_seconds)))
    if os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
        print(f"[BTC5M] WATCH TIMEOUT after {int(max_seconds)}s; job may not be terminal.", flush=True)
    return {"ok": False, "job_id": job_id, "state": "watch_timeout", "checks": checks,
            "status_errors": status_errors, "collect_errors": collect_errors, "status": last}


def _configure_visible_watcher_console(job_id: str, log_path: str) -> None:
    """Attach watcher stdout/stderr to its new Windows console and the durable log."""
    if os.name != "nt" or os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") != "1":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(f"BTC5M Worker - {job_id}")
    except Exception:
        pass
    try:
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        class _Tee:
            def __init__(self, console, file):
                self.console, self.file = console, file
            def write(self, value):
                try:
                    self.console.write(value); self.console.flush()
                except Exception:
                    pass
                try:
                    self.file.write(value); self.file.flush()
                except Exception:
                    pass
                return len(value)
            def flush(self):
                try: self.console.flush()
                except Exception: pass
                try: self.file.flush()
                except Exception: pass
            def isatty(self):
                try: return self.console.isatty()
                except Exception: return False
        sys.stdout = _Tee(sys.stdout, log)
        sys.stderr = _Tee(sys.stderr, log)
    except Exception:
        pass
    print(f"[BTC5M] {job_id}", flush=True)
    print("Waiting for second-PC job to finish and results to be collected...", flush=True)
    print("This window will close automatically when collection is complete.", flush=True)


def spawn_auto_collector(host: str, job_id: str, local_root: str | None = None) -> dict[str, Any]:
    log_root = _collect_root(local_root) / "_auto_collect_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"{job_id}.log"
    argv = [sys.executable, str(Path(__file__).resolve()), "--host", host, "_watch_collect", job_id,
            "--poll-seconds", str(AUTO_COLLECT_POLL_SECONDS), "--max-seconds", str(AUTO_COLLECT_MAX_SECONDS)]
    if local_root:
        argv += ["--local-root", str(Path(local_root).expanduser().resolve())]

    # Visible by default on the Windows research host. The window lifetime is
    # exactly the auto-collector watcher lifetime. BTC5M_WORKER_CONSOLE=0
    # restores the old hidden behavior.
    visible_console = (
        os.name == "nt" and
        os.environ.get("BTC5M_WORKER_CONSOLE", "1").strip().lower() not in {"0", "false", "off", "no"}
    )
    if visible_console:
        flags = (int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0)) |
                 int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        env = os.environ.copy()
        env["BTC5M_WORKER_CONSOLE_CHILD"] = "1"
        env["BTC5M_WORKER_CONSOLE_LOG"] = str(log_path)
        proc = subprocess.Popen(
            argv, cwd=str(LOCAL_ROOT), stdin=subprocess.DEVNULL,
            close_fds=True, creationflags=flags, env=env,
        )
        return {"started": True, "pid": proc.pid, "log": str(log_path),
                "console_visible": True, "console_title": f"BTC5M Worker - {job_id}"}

    flags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = (int(getattr(subprocess, "DETACHED_PROCESS", 0)) |
                 int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) |
                 int(getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab", buffering=0) as log:
        proc = subprocess.Popen(argv, cwd=str(LOCAL_ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, creationflags=flags, **kwargs)
    return {"started": True, "pid": proc.pid, "log": str(log_path), "console_visible": False}


def cmd_reconcile(host: str, local_root: str | None = None, limit: int = 50) -> Any:
    rows = cmd_status(host, None)
    if not isinstance(rows, list):
        return {"ok": False, "reason": "status_list_not_available", "status": rows}
    out = []
    for st in rows[:max(1, int(limit))]:
        if not isinstance(st, dict) or st.get("state") != "succeeded" or not st.get("job_id"):
            continue
        jid = str(st["job_id"])
        try:
            col = cmd_collect(host, jid, local_root, mode="reconcile")
            out.append({"job_id": jid, "collected": col})
        except Exception as ex:
            out.append({"job_id": jid, "error": f"{type(ex).__name__}:{ex}"})
    return {"ok": all("error" not in x for x in out), "checked": min(len(rows), max(1, int(limit))), "succeededSeen": len(out), "results": out}


def cmd_submit(
    host: str, argv: list[str], cwd: str, job_id: str | None, max_threads: int,
    min_free_ram_gb: float, max_start_cpu_pct: float, defer_if_copy_active: bool = True,
    resource_class: str = "cpu", gpu_index: int = 0, min_free_vram_mib: int = 4096,
    max_start_gpu_pct: float = 70.0, cancel_protected: bool = False, auto_collect: bool = True,
    local_root: str | None = None,
) -> Any:
    if not argv:
        raise ValueError("missing command after --")
    # Normalize the worker venv launcher so staged jobs never depend on cwd.
    a0 = str(argv[0]).replace("/", "\\").lower()
    if a0 in {r".venv\scripts\python.exe", r".\.venv\scripts\python.exe"}:
        argv = [REMOTE_PY, *argv[1:]]
    if resource_class not in {"cpu", "gpu"}:
        raise ValueError("resource_class must be cpu or gpu")
    if resource_class == "gpu":
        gp = cmd_gpu_probe(host)
        if not gp.get("available"):
            return {"accepted": False, "reason": "gpu_unavailable", "gpu_probe": gp}
    spec: dict[str, Any] = {
        "argv": argv, "cwd": cwd, "max_threads": max_threads,
        "min_free_ram_gb": min_free_ram_gb, "max_start_cpu_pct": max_start_cpu_pct,
        "defer_if_copy_active": defer_if_copy_active, "resource_class": resource_class,
        "gpu_index": gpu_index, "min_free_vram_mib": min_free_vram_mib,
        "max_start_gpu_pct": max_start_gpu_pct, "cancel_protected": bool(cancel_protected),
    }
    if job_id:
        spec["job_id"] = job_id
    result = parse_json_output(remote(host, ["submit", encode_spec(spec)]))
    if auto_collect and result.get("accepted") and result.get("job_id"):
        try:
            result["auto_collector"] = spawn_auto_collector(host, str(result["job_id"]), local_root)
        except Exception as ex:
            result["auto_collector"] = {"started": False, "error": f"{type(ex).__name__}:{ex}"}
    return result


def cmd_smoke(host: str) -> Any:
    job_id = f"smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    code = (
        "import json,os,platform,time,pathlib; "
        "r=pathlib.Path(os.environ['BTC5M_LAN_RESULT_DIR']); "
        "(r/'smoke.json').write_text(json.dumps({'smoke':'ok','host':platform.node(),'pid':os.getpid()})); "
        "print(json.dumps({'smoke':'ok','host':platform.node(),'result_dir':str(r)})); "
        "time.sleep(1); print('DONE')"
    )
    submitted = cmd_submit(host, ["python", "-c", code], ".", job_id, 2, 4.0, 90.0, False, "cpu", 0, 4096, 70.0, False, False)
    if not submitted.get("accepted"):
        return {"submitted": submitted}
    deadline = time.time() + 20
    last = None
    while time.time() < deadline:
        last = cmd_status(host, job_id)
        if last.get("state") in {"succeeded", "failed", "runner_error", "cancelled"}:
            break
        time.sleep(0.5)
    payload = {
        "submitted": submitted, "status": last,
        "stdout": cmd_tail(host, job_id, "stdout", 8192),
        "stderr": cmd_tail(host, job_id, "stderr", 8192),
    }
    if last and last.get("state") == "succeeded":
        payload["collected"] = cmd_collect(host, job_id)
    return payload


def cmd_harvest(host: str, job_ids: list[str], timeout_seconds: int = 180, poll_seconds: float = 1.0, local_root: str | None = None) -> Any:
    terminal={"succeeded","failed","runner_error","cancelled"}
    pending=set(job_ids); results: dict[str, Any]={}
    deadline=time.time()+max(1,int(timeout_seconds))
    while pending and time.time()<deadline:
        for jid in list(pending):
            try:
                st=cmd_status(host,jid)
            except Exception as ex:
                results[jid]={"state":"status_error","error":f"{type(ex).__name__}:{ex}"}
                pending.remove(jid); continue
            if st.get("state") in terminal:
                item={"status":st}
                if st.get("state")=="succeeded":
                    try:
                        item["collected"]=cmd_collect(host,jid,local_root)
                    except Exception as ex:item["collect_error"]=f"{type(ex).__name__}:{ex}"
                else:
                    try:item["stdout"]=cmd_tail(host,jid,"stdout",4096)
                    except Exception:pass
                    try:item["stderr"]=cmd_tail(host,jid,"stderr",4096)
                    except Exception:pass
                results[jid]=item; pending.remove(jid)
        if pending: time.sleep(max(0.2,float(poll_seconds)))
    for jid in sorted(pending):
        try:st=cmd_status(host,jid)
        except Exception as ex:st={"state":"status_error","error":f"{type(ex).__name__}:{ex}"}
        results[jid]={"status":st,"harvestTimedOut":True}
    return {"ok":not pending,"jobCount":len(job_ids),"completed":len(job_ids)-len(pending),"pending":sorted(pending),"results":results}



def cmd_wave(host: str, plan_path: str, max_parallel: int = 4, poll_seconds: float = 0.5, local_root: str | None = None) -> Any:
    """Keep worker lanes saturated: submit queued jobs, collect each terminal job immediately, backfill freed slots."""
    plan=json.loads(Path(plan_path).read_text(encoding="utf-8"))
    jobs=plan.get("jobs") if isinstance(plan,dict) else plan
    progress_artifact=(plan.get("progress_artifact") if isinstance(plan,dict) else None)
    postprocess=(plan.get("postprocess") if isinstance(plan,dict) else None)
    if not isinstance(jobs,list) or not jobs:
        raise ValueError("wave plan must contain non-empty jobs list")
    max_parallel=max(1,int(max_parallel))
    queued=list(jobs); running: dict[str,dict[str,Any]]={}; results: dict[str,Any]={}; submit_order=[]
    terminal={"succeeded","failed","runner_error","cancelled"}
    t0=time.time()
    def write_progress() -> None:
        if not progress_artifact:
            return
        pp=Path(progress_artifact)
        if not pp.is_absolute():
            pp=LOCAL_ROOT/pp
        pp.parent.mkdir(parents=True,exist_ok=True)
        payload={
            "version":"BTC5M_LAN_WAVE_PROGRESS_V1",
            "plan":str(plan_path),
            "jobCount":len(jobs),
            "completed":len(results),
            "running":sorted(running),
            "queued":len(queued),
            "elapsedSeconds":round(time.time()-t0,3),
            "updatedAt":time.time(),
        }
        pp.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding="utf-8")
    def launch_one(job: dict[str,Any]) -> None:
        jid=str(job.get("job_id") or job.get("jobId") or "").strip()
        argv=list(job.get("argv") or [])
        if not jid or not argv: raise ValueError(f"invalid wave job: {job}")
        try:
            sub=cmd_submit(host,argv,str(job.get("cwd") or "."),jid,int(job.get("max_threads") or 4),float(job.get("min_free_ram_gb") or 6.0),float(job.get("max_start_cpu_pct") or 95.0),bool(job.get("defer_if_copy_active",True)),str(job.get("resource") or "cpu"),int(job.get("gpu_index") or 0),int(job.get("min_free_vram_mib") or 2048),float(job.get("max_start_gpu_pct") or 95.0),False,False,local_root)
            submit_order.append(jid)
            if sub.get("accepted"):
                running[jid]={"job":job,"submitted":sub,"submittedAt":time.time()}
            else:
                results[jid]={"submitted":sub,"state":"not_accepted"}
        except RuntimeError as ex:
            if "job already exists" not in str(ex):
                raise
            st=cmd_status(host,jid); submit_order.append(jid+":ATTACH")
            if st.get("state") in {"succeeded","failed","runner_error","cancelled"}:
                item={"status":st,"attachedExisting":True,"queueResidenceSeconds":0.0}
                if st.get("state")=="succeeded":
                    try:
                        item["collected"]=cmd_collect(host,jid,local_root)
                        req=job.get("required_artifact")
                        if req:
                            ap=Path(item["collected"]["local_path"])/str(req)
                            item["requiredArtifact"]=str(ap); item["artifactPresent"]=ap.exists()
                    except Exception as ce:item["collect_error"]=f"{type(ce).__name__}:{ce}"
                results[jid]=item
            else:
                running[jid]={"job":job,"submitted":{"accepted":True,"attachedExisting":True},"submittedAt":time.time()}
    while queued or running:
        while queued and len(running)<max_parallel:
            launch_one(queued.pop(0))
        progressed=False
        for jid in list(running):
            try: st=cmd_status(host,jid)
            except Exception as ex:
                results[jid]={"state":"status_error","error":f"{type(ex).__name__}:{ex}"}; running.pop(jid,None); progressed=True; continue
            if st.get("state") in terminal:
                item={"status":st,"queueResidenceSeconds":time.time()-running[jid]["submittedAt"]}
                if st.get("state")=="succeeded":
                    try:
                        item["collected"]=cmd_collect(host,jid,local_root)
                        req=running[jid]["job"].get("required_artifact")
                        if req:
                            ap=Path(item["collected"]["local_path"])/str(req)
                            item["requiredArtifact"]=str(ap); item["artifactPresent"]=ap.exists()
                    except Exception as ex:item["collect_error"]=f"{type(ex).__name__}:{ex}"
                else:
                    try:item["stdout"]=cmd_tail(host,jid,"stdout",4096)
                    except Exception:pass
                    try:item["stderr"]=cmd_tail(host,jid,"stderr",4096)
                    except Exception:pass
                results[jid]=item; running.pop(jid,None); progressed=True; write_progress()
                # immediately backfill the freed lane before scanning again
                if queued and len(running)<max_parallel: launch_one(queued.pop(0))
        if not progressed and running: time.sleep(max(0.2,float(poll_seconds)))
    succeeded=sum(1 for x in results.values() if (x.get("status") or {}).get("state")=="succeeded" and x.get("artifactPresent") is not False)
    write_progress()
    post_result=None
    if postprocess:
        pargv=list(postprocess.get("argv") or [])
        if not pargv:
            raise ValueError("wave postprocess requires argv")
        pcwd=str(postprocess.get("cwd") or ".")
        ptimeout=int(postprocess.get("timeout_seconds") or 30)
        cp=subprocess.run(pargv,cwd=str((LOCAL_ROOT/pcwd).resolve()),capture_output=True,text=True,timeout=ptimeout,encoding="utf-8",errors="replace")
        post_result={
            "returnCode":cp.returncode,
            "stdout":cp.stdout[-int(postprocess.get("max_output_bytes") or 12000):],
            "stderr":cp.stderr[-4000:],
        }
        req=postprocess.get("required_artifact")
        if req:
            ap=Path(req)
            if not ap.is_absolute(): ap=LOCAL_ROOT/ap
            post_result["requiredArtifact"]=str(ap); post_result["artifactPresent"]=ap.exists()
    return {"ok":succeeded==len(jobs) and (post_result is None or post_result.get("returnCode")==0),"version":"BTC5M_LAN_WAVE_V2","jobCount":len(jobs),"succeeded":succeeded,"elapsedSeconds":round(time.time()-t0,3),"maxParallel":max_parallel,"submitOrder":submit_order,"results":results,"postprocess":post_result,"progressArtifact":progress_artifact}

def emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--host", default=DEFAULT_HOST)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe")
    sub.add_parser("gpu-probe")
    st = sub.add_parser("status"); st.add_argument("job_id", nargs="?")
    tl = sub.add_parser("tail"); tl.add_argument("job_id"); tl.add_argument("--stream", choices=["stdout", "stderr"], default="stdout"); tl.add_argument("--max-bytes", type=int, default=16384)
    ca = sub.add_parser("cancel"); ca.add_argument("job_id"); ca.add_argument("--token"); ca.add_argument("--force", action="store_true")
    sg = sub.add_parser("stage"); sg.add_argument("local_path")
    co = sub.add_parser("collect"); co.add_argument("job_id"); co.add_argument("--local-root")
    sub.add_parser("smoke")
    rc = sub.add_parser("reconcile"); rc.add_argument("--local-root"); rc.add_argument("--limit", type=int, default=50)
    aw = sub.add_parser("_watch_collect"); aw.add_argument("job_id"); aw.add_argument("--local-root"); aw.add_argument("--poll-seconds", type=float, default=AUTO_COLLECT_POLL_SECONDS); aw.add_argument("--max-seconds", type=int, default=AUTO_COLLECT_MAX_SECONDS)
    hv = sub.add_parser("harvest"); hv.add_argument("job_ids", nargs="+"); hv.add_argument("--timeout-seconds", type=int, default=180); hv.add_argument("--poll-seconds", type=float, default=1.0); hv.add_argument("--local-root")
    wv = sub.add_parser("wave"); wv.add_argument("plan_path"); wv.add_argument("--max-parallel", type=int, default=4); wv.add_argument("--poll-seconds", type=float, default=0.5); wv.add_argument("--local-root")
    su = sub.add_parser("submit")
    su.add_argument("--cwd", default="."); su.add_argument("--job-id")
    su.add_argument("--max-threads", type=int, default=12); su.add_argument("--min-free-ram-gb", type=float, default=8.0)
    su.add_argument("--max-start-cpu-pct", type=float, default=80.0); su.add_argument("--allow-during-copy", action="store_true")
    su.add_argument("--resource", choices=["cpu", "gpu"], default="cpu"); su.add_argument("--gpu-index", type=int, default=0)
    su.add_argument("--min-free-vram-mib", type=int, default=4096); su.add_argument("--max-start-gpu-pct", type=float, default=70.0); su.add_argument("--protect-cancel", action="store_true")
    su.add_argument("--no-auto-collect", action="store_true"); su.add_argument("--local-root")
    su.add_argument("argv", nargs=argparse.REMAINDER)
    ns = ap.parse_args(); host = ns.host
    if ns.cmd == "_watch_collect" and os.environ.get("BTC5M_WORKER_CONSOLE_CHILD") == "1":
        _configure_visible_watcher_console(ns.job_id, os.environ.get("BTC5M_WORKER_CONSOLE_LOG", ""))
    if ns.cmd == "probe": emit(cmd_probe(host))
    elif ns.cmd == "gpu-probe": emit(cmd_gpu_probe(host))
    elif ns.cmd == "status": emit(cmd_status(host, ns.job_id))
    elif ns.cmd == "tail": emit(cmd_tail(host, ns.job_id, ns.stream, ns.max_bytes))
    elif ns.cmd == "cancel": emit(cmd_cancel(host, ns.job_id, ns.token, ns.force))
    elif ns.cmd == "stage": emit(cmd_stage(host, ns.local_path))
    elif ns.cmd == "collect": emit(cmd_collect(host, ns.job_id, ns.local_root))
    elif ns.cmd == "smoke": emit(cmd_smoke(host))
    elif ns.cmd == "reconcile": emit(cmd_reconcile(host, ns.local_root, ns.limit))
    elif ns.cmd == "_watch_collect": emit(cmd_auto_collect_watch(host, ns.job_id, ns.local_root, ns.poll_seconds, ns.max_seconds))
    elif ns.cmd == "harvest": emit(cmd_harvest(host, ns.job_ids, ns.timeout_seconds, ns.poll_seconds, ns.local_root))
    elif ns.cmd == "wave": emit(cmd_wave(host, ns.plan_path, ns.max_parallel, ns.poll_seconds, ns.local_root))
    elif ns.cmd == "submit":
        argv = ns.argv[1:] if ns.argv and ns.argv[0] == "--" else ns.argv
        emit(cmd_submit(host, argv, ns.cwd, ns.job_id, ns.max_threads, ns.min_free_ram_gb,
                        ns.max_start_cpu_pct, not ns.allow_during_copy, ns.resource, ns.gpu_index,
                        ns.min_free_vram_mib, ns.max_start_gpu_pct, ns.protect_cancel, not ns.no_auto_collect, ns.local_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
