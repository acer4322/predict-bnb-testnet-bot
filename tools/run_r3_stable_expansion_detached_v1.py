from pathlib import Path
import subprocess, json, time, sys, traceback
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/r3_v0'
status=D/'r3_stable_expansion_train_status_v1.json'
log=D/'r3_stable_expansion_train_v1.log'
status.write_text(json.dumps({'state':'RUNNING','startedAt':time.time(),'artifact':'data/research/r3_v0/r3_stable_expansion_teacher_v1_full_report.json','log':'data/research/r3_v0/r3_stable_expansion_train_v1.log'},indent=2),encoding='utf-8')
try:
    with log.open('w',encoding='utf-8') as f:
        cp=subprocess.run([sys.executable,str(ROOT/'tools/train_r3_stable_expansion_from_shards_v1.py')],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
    status.write_text(json.dumps({'state':'COMPLETE' if cp.returncode==0 else 'FAILED','exitCode':cp.returncode,'finishedAt':time.time(),'artifact':'data/research/r3_v0/r3_stable_expansion_teacher_v1_full_report.json','log':'data/research/r3_v0/r3_stable_expansion_train_v1.log'},indent=2),encoding='utf-8')
except Exception as e:
    status.write_text(json.dumps({'state':'FAILED','error':repr(e),'traceback':traceback.format_exc(),'finishedAt':time.time(),'artifact':'data/research/r3_v0/r3_stable_expansion_teacher_v1_full_report.json','log':'data/research/r3_v0/r3_stable_expansion_train_v1.log'},indent=2),encoding='utf-8')
