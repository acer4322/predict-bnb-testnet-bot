from __future__ import annotations
import json,sys,subprocess,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCAN=ROOT/'data/research/r4_v0/hourly/r4_baseline_cross_candidates_checkpoint_v2.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_value_paired_forks_checkpoint_v2.json'
def save(rows): OUT.write_text(json.dumps({'version':'R4_CROSS_VALUE_PAIRED_FORKS_CHECKPOINT_V2','researchOnly':True,'rows':rows},indent=2),encoding='utf-8')
def main():
    d=json.loads(SCAN.read_text()); targets=[]
    for m in d['markets']:
        if not m.get('seedEquivalent'): continue
        for e in m.get('candidates',[]):
            if float(e.get('floor',0))<0: targets.append((int(m['marketId']),int(e['atMs']),e))
    rows=[]
    for i,(mid,at,e) in enumerate(targets,1):
        p=subprocess.run([sys.executable,str(ROOT/'tools/r4_cross_for_value_single_fork_v1.py'),str(mid),str(at),'--force-treatment'],cwd=str(ROOT),capture_output=True,text=True,env={**os.environ,'PYTHONWARNINGS':'ignore'})
        rec={'marketId':mid,'atMs':at,'source':e,'returncode':p.returncode}
        try:
            lines=[x for x in p.stdout.splitlines() if x.strip()]
            objs=[]
            for line in lines:
                try: objs.append(json.loads(line))
                except Exception: pass
            z=next((x for x in reversed(objs) if isinstance(x,dict) and ('deltaFloor' in x or 'class' in x)),None)
            if z is None: raise ValueError('no result json')
            rec.update(z)
        except Exception: rec['error']=(p.stderr or p.stdout)[-4000:]
        rows.append(rec); save(rows)
        print(json.dumps({'i':i,'n':len(targets),'marketId':mid,'atMs':at,'deltaFloor':rec.get('deltaFloor'),'class':rec.get('class'),'error':bool(rec.get('error'))}),flush=True)
    print(json.dumps({'targets':len(targets),'rows':len(rows)}),flush=True)
if __name__=='__main__': main()
