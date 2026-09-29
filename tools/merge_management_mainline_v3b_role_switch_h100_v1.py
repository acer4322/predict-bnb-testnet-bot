from __future__ import annotations
import json,statistics,collections
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARTS=[ROOT/f'data/research/lan_worker_returns/management-mainline-v3b-role-switch-fork-h100-p{i}-20260907-v1/result.json' for i in range(1,5)]
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_H100_MERGED_V1_20260907.json'
EPS=1e-9

def q(xs,a):
 xs=sorted(xs);return xs[min(len(xs)-1,max(0,int(a*(len(xs)-1))))] if xs else None

def main():
 rows=[];sources=[]
 for p in PARTS:
  d=json.loads(p.read_text(encoding='utf-8'));sources.append({'path':str(p.relative_to(ROOT)),'stateCount':d['stateCount'],'allCorrectnessPass':d['allCorrectnessPass']});rows.extend(d['rows'])
 rows.sort(key=lambda r:(int(r['stateSpec']['t']),int(r['marketId'])))
 ks=['floor','best','gap','favoredPayoff','weakPayoff','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty']
 agg={}
 for k in ks:
  xs=[float(r['reexpandMinusRepairTerminalVector'][k]) for r in rows]
  agg[k]={'sum':sum(xs),'mean':statistics.mean(xs),'median':statistics.median(xs),'p10':q(xs,.1),'p90':q(xs,.9),'positive':sum(x>EPS for x in xs),'negative':sum(x<-EPS for x in xs),'tie':sum(abs(x)<=EPS for x in xs)}
 cls=collections.Counter();dom=collections.Counter();res=collections.Counter()
 for r in rows:
  v=r['reexpandMinusRepairTerminalVector'];df=float(v['floor']);db=float(v['best'])
  if abs(df)<=EPS and abs(db)<=EPS:c='TIE'
  elif df>=-EPS and db>=-EPS and (df>EPS or db>EPS):c='REEXPAND_DOMINATES'
  elif df<=EPS and db<=EPS and (df<-EPS or db<-EPS):c='REPAIR_DOMINATES'
  else:c='TRADEOFF'
  r['floorBestClass']=c;cls[c]+=1
  native=str(r['stateSpec']['nativeClass']);dom[f'{native}|{c}']+=1
  for b,z in r['branchResolution'].items():res[f'{b}:{None if z is None else z["kind"]}']+=1
 progress=[float(r['stateSpec']['repairProgressFrac']) for r in rows]
 out={'version':'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_H100_MERGED_V1_20260907','researchOnly':True,'runtimeAuthority':False,'sources':sources,
      'stateCount':len(rows),'allCorrectnessPass':all(r['valid'] for r in rows),'nativeClasses':dict(collections.Counter(r['stateSpec']['nativeClass'] for r in rows)),
      'progressDistribution':{'p10':q(progress,.1),'median':statistics.median(progress),'p90':q(progress,.9)},
      'terminalContrastReexpandMinusRepair':agg,'floorBestClassification':dict(cls),'nativeByFloorBestClass':dict(dom),'branchResolutionKinds':dict(res),'rows':rows,
      'boundary':['first chronological eligible no-pending-Active state per consumed H100 market','same-prefix current V3B exact fork','REEXPAND minus REPAIR is vector evidence only','no scalar reward/no winner/Target/future input','consumed development cohort only','NEW24-B untouched','no runtime authority/no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT.relative_to(ROOT)),'stateCount':len(rows),'allCorrectnessPass':out['allCorrectnessPass'],'floorBestClassification':out['floorBestClassification']},ensure_ascii=False))
if __name__=='__main__':main()
