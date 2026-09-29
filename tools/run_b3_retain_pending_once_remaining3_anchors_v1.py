from __future__ import annotations
import copy, importlib.util, json, os, sys, tempfile, zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_b3_retain_pending_once_primary1824852_v1.py'
if STAGED.exists():
 sp=importlib.util.spec_from_file_location('retain_primary_staged',STAGED);rp=importlib.util.module_from_spec(sp);sys.modules[sp.name]=rp;sp.loader.exec_module(rp)
else:
 from tools import run_b3_retain_pending_once_primary1824852_v1 as rp
MIDS=[1824758,1825994,1825962]

def row_for(mid,tape,spec,src,raw,bc):
 rp.MID=mid
 seam=copy.deepcopy(src['frozenSeam']);rr=raw['row'];manifest={'rawBundleIdentity':copy.deepcopy(rr['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in rr['R0']]}
 C0=rp.run_branch(tape,spec,seam,manifest,'C0_DISABLED')
 cpar={'behavior':C0['behaviorLedgerDigest']==src['branches']['P']['behaviorLedgerDigest'],'terminal':rp.stable(C0['terminal'])==rp.stable(src['branches']['P']['terminalEconomics']),'numeric':rp.stable(C0['numeric'])==rp.stable(src['branches']['P']['numeric']),'correctness':C0['correctnessPass']}
 dt=C0['detectedTStar'];reachable=dt is not None
 if reachable:R=rp.run_branch(tape,spec,seam,manifest,'R',None,dt)
 else:R=rp.run_branch(tape,spec,seam,manifest,'C0_DISABLED_UNREACHABLE_R')
 physical=None
 if reachable and R.get('retain') is not None:
  cp=dt['post'];rpst=R['retain']['post'];physical={'phaseOrdinal':R['retain']['phaseOrdinal'],'eventTimestampMs':R['retain']['eventTimestampMs'],'c0SubmitDelta':int(cp['submits'])-int(dt['pre']['submits']),'rSubmitDelta':int(rpst['submits'])-int(R['retain']['pre']['submits']),'c0SlotCount':len(cp['slots']),'rSlotCount':len(rpst['slots']),'c0ActiveKey':None if cp['qLadder'] is None else cp['qLadder'].get('activeKey'),'rPendingPreserved':rpst['qPendingActive'] is not None and rpst['qLadder'] is not None and rpst['qLadder'].get('route')=='PENDING_ACTIVE'}
 firstPass=physical is not None and physical['c0SubmitDelta']==1 and physical['rSubmitDelta']==0 and physical['c0ActiveKey'] is not None and physical['rPendingPreserved']
 kdelta={k:float(R['numeric'][k])-float(C0['numeric'][k]) for k in rp.K};costEffect=any(abs(v)>rp.CASH_TOL for v in kdelta.values())
 tails=bc['newTail'];tailChecks={k:float(R['numeric'][k])<=float(v)+rp.CASH_TOL for k,v in tails.items() if k in R['numeric'] and v is not None}
 N=src['branches']['N']['terminalEconomics'];econ={'RminusC0':{'deltaU':float(R['terminal']['U'])-float(C0['terminal']['U']),'deltaD':float(R['terminal']['D'])-float(C0['terminal']['D'])},'RminusN':{'deltaU':float(R['terminal']['U'])-float(N['U']),'deltaD':float(R['terminal']['D'])-float(N['D'])}}
 anti=reachable and bool(R['activity']['trade'] and R['activity']['twoSided'] and R['activity']['repeated'] and R['sameH0Service']['h0ServiceReplacedOrCompleted'] and R['nativeReentry'] is not None)
 if not all(cpar.values()) or not R['correctnessPass']:verdict='CORRECTNESS_OR_PROVENANCE_STOP'
 elif not reachable:verdict='ACTION_NOT_REACHABLE'
 elif not firstPass:verdict='ACTION_EXERCISED_NO_PHYSICAL_FIRST_STAGE'
 elif R['nativeReentry'] is None:verdict='NO_NATIVE_REENTRY_BEFORE_TERMINAL'
 elif not anti:verdict='ANTI_COLLAPSE_NOT_IDENTIFIED'
 elif not costEffect:verdict='ACTION_EXERCISED_NO_BUDGET_PATH_EFFECT'
 else:verdict='ANCHOR_MECHANISM_EXERCISED'
 return {'marketId':mid,'C0DisabledParity':cpar,'actionReachable':reachable,'detectedTStar':dt,'physicalFirstStage':physical,'physicalFirstStagePass':firstPass,'KDeltaRminusC0':kdelta,'costPathEffectPass':costEffect,'individualTailChecksR':tailChecks,'individualTailPassR':all(tailChecks.values()),'economic':econ,'antiCollapseLocalPass':anti,'R':R,'C0':C0,'verdict':verdict,'tags':([] if all(tailChecks.values()) else ['INDIVIDUAL_PLVAC_BUDGET_FAIL'])}

def main():
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--baseline-compact',required=True);ap.add_argument('--source-dir',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 co=json.load(open(a.cohort,encoding='utf-8'));specmap={int(x['marketId']):x for x in co['states']};bc=json.load(open(a.baseline_compact,encoding='utf-8'));sd=Path(a.source_dir);rows=[]
 with tempfile.TemporaryDirectory(prefix='retain_remaining3_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   for mid in MIDS:z.extract(f'tapes/{mid}.json.xz',root)
  for mid in MIDS:
   src=json.load(open(sd/f'B3_PLVAC_FORMAL_SOURCE_{mid}_20260908.json',encoding='utf-8'));raw=json.load(open(sd/f'B3_PLVAC_RAW_IDENTITY_{mid}_20260908.json',encoding='utf-8'));row=row_for(mid,root/'tapes'/f'{mid}.json.xz',specmap[mid],src,raw,bc);rows.append(row);print(json.dumps({'marketId':mid,'verdict':row['verdict'],'reachable':row['actionReachable'],'tStar':None if row['detectedTStar'] is None else [row['detectedTStar']['phaseOrdinal'],row['detectedTStar']['eventTimestampMs']],'KDelta':row['KDeltaRminusC0'],'tailPass':row['individualTailPassR'],'econ':row['economic'],'TBR':row['R']['activity']},ensure_ascii=False),flush=True)
 stop=any(r['verdict']=='CORRECTNESS_OR_PROVENANCE_STOP' for r in rows);out={'version':'B3_RETAIN_PENDING_ONCE_REMAINING3_ANCHORS_V1_20260908','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'branchEquivalents':6,'correctnessStop':stop,'boundary':['N frozen formal source reused; no N replay','C0-disabled + R only per anchor','fixed order 1824758->1825994->1825962','no replacement/no second retain/no fresh/no8781']}
 op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'correctnessStop':stop,'verdicts':{str(r['marketId']):r['verdict'] for r in rows}},ensure_ascii=False))
if __name__=='__main__':main()
