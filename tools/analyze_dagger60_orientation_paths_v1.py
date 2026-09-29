from __future__ import annotations
import json
from pathlib import Path
SRC={'TEST20':'data/research/lan_worker_returns/dagger60-lp-test20-extreme-trace-20260909-v1/trace.json','FRESH101':'data/research/lan_worker_returns/dagger60-lp-fresh101-extreme-trace-20260909-v1/trace.json','EXTERNAL24':'data/research/lan_worker_returns/dagger60-lp-external24-trace-20260909-v1/trace.json'}
OUT=Path('data/research/r4_v0/p0_provenance_v1/DAGGER60_LOCAL_PENDING_FRESH101_RETEST_EXTREME_PACKET_V1_20260909/ORIENTATION_PATHS.json')
def summ(rs):
 if not rs:return {'n':0}
 return {'n':len(rs),'pnl':sum(r['pnl'] for r in rs),'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanPairCoverage':sum(r['pairCoverage'] for r in rs)/len(rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':sum(r['absNet'] for r in rs)/len(rs)}
out={}
for name,p in SRC.items():
 d=json.load(open(p));g={'INITIAL_CORRECT_TERMINAL_CORRECT':[],'INITIAL_CORRECT_TERMINAL_WRONG':[],'INITIAL_WRONG_TERMINAL_CORRECT':[],'INITIAL_WRONG_TERMINAL_WRONG':[]}
 for m in d['markets']:
  t=m['terminal'];ff=m['fillEvents'][0]['side'] if m['fillEvents'] else None;dom='UP' if t['up']>t['down']+1e-9 else 'DOWN' if t['down']>t['up']+1e-9 else None
  if ff is None or dom is None:continue
  ic=ff==t['winner'];tc=dom==t['winner'];key=('INITIAL_CORRECT_' if ic else 'INITIAL_WRONG_')+('TERMINAL_CORRECT' if tc else 'TERMINAL_WRONG');g[key].append(t)
 out[name]={k:summ(v) for k,v in g.items()}
 out[name]['initialFillMatchRate']=(g['INITIAL_CORRECT_TERMINAL_CORRECT'].__len__()+g['INITIAL_CORRECT_TERMINAL_WRONG'].__len__())/len(d['markets'])
 out[name]['terminalDominantMatchRate']=(g['INITIAL_CORRECT_TERMINAL_CORRECT'].__len__()+g['INITIAL_WRONG_TERMINAL_CORRECT'].__len__())/len(d['markets'])
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
