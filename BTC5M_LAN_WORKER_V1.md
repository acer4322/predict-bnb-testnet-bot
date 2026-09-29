# BTC5M LAN Worker V1

## Node

- SSH alias: `btc5m-worker`
- Host: `192.168.68.52` (user-confirmed and probed 2026-09-10; older `.51`/`.53` addresses are stale)
- Windows user: `man`
- Hostname: `DESKTOP-JIERAGF`
- Worker root: `C:\BTC5M-worker`
- Python: `C:\BTC5M-worker\.venv\Scripts\python.exe` (3.13.15)
- CPU: AMD Ryzen 7 3800XT, 8C/16T
- RAM: ~40 GB
- GPU: NVIDIA GeForce RTX 3070 Ti 8 GB

## V1 Components

- `tools/btc5m_lan_worker_agent_v1.py`: runs on the worker; job spool, resource gate, background execution, status/logs/cancel.
- `tools/btc5m_lan_dispatch_v1.py`: runs on MSI; probe/submit/status/tail/cancel/smoke plus staging/result collection over SSH/SCP.

Worker state lives under:

`C:\BTC5M-worker\.lan_worker_v1\jobs\<job_id>`

Each job stores `spec.json`, `status.json`, `stdout.log`, and `stderr.log`. Each job also gets a dedicated result directory at `C:\\BTC5M-worker\\.lan_worker_v1\\results\\<job_id>` exposed to the process as `BTC5M_LAN_RESULT_DIR`. Staged inputs live under `.lan_worker_v1\\staging`.

## Safety defaults

- max logical threads per job: 12
- minimum free RAM to start: 8 GB
- maximum CPU load at submit: 80%
- thread-heavy libraries receive the same thread cap through OMP/OpenBLAS/MKL/NumExpr environment variables
- worker child process priority is set below normal
- active `robocopy.exe` is treated as a copy/data-build guard unless explicitly overridden
- GPU jobs are gated on NVIDIA availability, free VRAM >= 4096 MiB, and start GPU utilization <= 70% by default
- GPU jobs receive `CUDA_VISIBLE_DEVICES` and `BTC5M_LAN_RESOURCE_CLASS=gpu`

These are start-time guards, not a hard RAM quota. Large research runs should still be staged and monitored.

## Commands from MSI

Read this runbook before using the worker. The machine normally sleeps. If the
first **probe** does not answer, allow a short wake-up interval (about 30 seconds)
and retry the probe before reporting it unavailable. This is connection handling,
not a trading timer. On 2026-09-10 the second connection answered; `.52` presented
the same trusted ED25519 host key previously recorded for `.53`, and reported
`DESKTOP-JIERAGF`.

Do not treat a timed-out **submit** like a failed probe: check the known job ID's
status/tail/result before any repeat submit. Never bypass SSH host-key checking.
If the second probe still fails, inspect connectivity/state rather than repeatedly
launching work. Current root-bottleneck research overrides the general thread
default below: explicitly pass `--max-threads 4`, and keep parallel jobs <=4.

Probe worker:

```powershell
python tools\btc5m_lan_dispatch_v1.py probe
```

Smoke test:

```powershell
python tools\btc5m_lan_dispatch_v1.py smoke
```

Submit a Python research job:

```powershell
python tools\btc5m_lan_dispatch_v1.py submit --job-id example --cwd . -- python tools\some_research.py
```

Check status:

```powershell
python tools\btc5m_lan_dispatch_v1.py status example
```

Read output:

```powershell
python tools\btc5m_lan_dispatch_v1.py tail example
```

Cancel:

```powershell
python tools\btc5m_lan_dispatch_v1.py cancel example
```

## Current policy

The worker is for research/replay/training/simulation. Do not run Echtgeld/live-order authority on the worker. Keep live services and source-of-truth state on MSI. Copy only the datasets needed by each research job to the worker's local disk, and return compact artifacts/results to MSI.

## Data staging and result return

Stage a file or directory to the worker local SSD:

```powershell
python tools\btc5m_lan_dispatch_v1.py stage path\to\input
```

The dispatcher returns the worker-relative staging path. Research jobs should read staged data locally rather than repeatedly scanning the SMB share.

Collect a completed job result directory back to MSI:

```powershell
python tools\btc5m_lan_dispatch_v1.py collect <job_id>
```

Default return location on MSI: `data\research\lan_worker_returns\<job_id>`. The smoke test now validates submit -> background execution -> result file -> SCP return end-to-end.

## GPU routing

Probe GPU capability:

```powershell
python tools\btc5m_lan_dispatch_v1.py gpu-probe
```

Submit a GPU-class job:

```powershell
python tools\btc5m_lan_dispatch_v1.py submit --resource gpu --job-id example-gpu -- python tools\some_gpu_research.py
```

Current node: RTX 3070 Ti 8 GB, NVIDIA driver 591.86, driver-reported CUDA 13.1. GPU routing is ready, but PyTorch is intentionally not installed yet. Install a workload-specific CUDA-capable framework only when a research task actually benefits from it. Current EBM/HGB/scikit-learn research remains primarily CPU-bound.


## Research sleep guard and progress health (2026-09-18)

The agent now holds a temporary ES_CONTINUOUS | ES_SYSTEM_REQUIRED request while each research job runs. It does not request an awake display or change the Windows sleep plan; normal sleep eligibility returns when the job ends. Explicit user sleep, shutdown, or connectivity failures are not prevented.

Each known job has jobs/<job_id>/heartbeat.json updated about every 10 seconds. It separates runner liveness, research progress age, stdout/stderr growth, and optional process-tree CPU samples. `status <job_id>` includes it and marks a running heartbeat stale after 45 seconds. A fresh heartbeat does not prove research progress. These are execution-health intervals, not strategy timing thresholds.

On a submit timeout, check that exact job ID first. Do not resubmit merely because the request timed out. Collect already-completed outputs before creating successors. Python child logs default to UTF-8 and unbuffered output.

Patch, tests, original bytes, and rollback hashes: data/research/v49_resume_health_20260918_r18/. Original worker-agent SHA-256: 6996a1c439f163070c0f916979b25106247e995e95663854e1ec597472e84323. This change affects the research agent only, not 8781 or any live strategy.
