from __future__ import annotations
import runpy, sys, threading, time, json

if len(sys.argv) < 2:
    raise SystemExit('usage: run_with_heartbeat_v1.py TARGET [ARGS...]')
target = sys.argv.pop(1)
stop = threading.Event()

def heartbeat() -> None:
    print(json.dumps({'heartbeat':'WRAPPER_START','target':target,'ts':time.time()}), flush=True)
    while not stop.wait(10):
        print(json.dumps({'heartbeat':'WRAPPER_ALIVE','target':target,'ts':time.time()}), flush=True)

threading.Thread(target=heartbeat, daemon=True).start()
try:
    runpy.run_path(target, run_name='__main__')
finally:
    stop.set()
