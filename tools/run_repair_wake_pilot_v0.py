from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(args):
    p=subprocess.run([sys.executable,*args],cwd=ROOT,text=True,capture_output=True,timeout=25)
    if p.returncode!=0:
        print(p.stdout); print(p.stderr,file=sys.stderr); raise SystemExit(p.returncode)
    if p.stdout.strip(): print(p.stdout.strip())

def main():
    run(['tools/patch_closed_loop_execution_tape_v1.py'])
    run(['-m','py_compile','tools/hftbacktest_cap100_closed_loop_v0.py','tools/hftbacktest_repair_wake_teacher_v0.py','tools/train_execution_aware_repair_wake_pilot_v0.py'])
    run(['tools/hftbacktest_cap100_closed_loop_v0.py','--market-id','1513668','--taker-mode','hft'])
if __name__=='__main__': main()
