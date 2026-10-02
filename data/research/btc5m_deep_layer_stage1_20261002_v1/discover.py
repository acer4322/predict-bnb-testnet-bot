"""Read-only worker/source pin for the explicitly authorized Codex takeover."""
import importlib.util
import json
from pathlib import Path

P = Path(__file__).resolve().parent
ROOT = P.parents[2]
s = importlib.util.spec_from_file_location('transport', ROOT / 'data/research/btc5m_public_cost_memory_20260921_r87/dispatch.py')
t = importlib.util.module_from_spec(s)
s.loader.exec_module(t)
JOB = 'btc5m-deep-layer-stage1-20261002-v1'
