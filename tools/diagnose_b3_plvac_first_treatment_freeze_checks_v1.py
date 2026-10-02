from __future__ import annotations
import argparse,copy,json,tempfile,zipfile,sys
from pathlib import Path
STAGE=Path(__file__).resolve().parent; ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if (STAGE/'tools').exists():sys.path.insert(0,str(STAGE))
elif str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_b3_handoff_native_bundle_oneshot_smoke4_v1 as old
MID=1824758

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--cohort',required=True);ap.add_argument('--baseline-result',required=True);ap.add_argument('--raw-manifest',required=True);a=ap.parse_args()
 base=json.loads(Path(a.baseline_result).read_text(encoding='utf-8')); seam=next(x for x in base['rows'] if int(x['marketId'])==MID)['seam']; raw=json.loads(Path(a.raw_manifest).read_text(encoding='utf-8'))['row']; co=json.loads(Path(a.cohort).read_text(encoding='utf-8')); spec=next(x for x in co['states'] if int(x['marketId'])==MID)
 manifest={'rawBundleIdentity':copy.deepcopy(raw['rawBundleIdentity']),'compactLocatorHash':seam['stateHash'],'behaviorPrefixDigest':seam['behaviorPrefixDigest'],'R0':[{k:copy.deepcopy(v) for k,v in x.items() if k!='paymentClocks'} for x in raw['R0']]}
 with tempfile.TemporaryDirectory(prefix='diag_freeze_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:z.extract(f'tapes/{MID}.json.xz',root)
  sim=old.B3Fork(root/'tapes'/f'{MID}.json.xz',spec,'N',seam,manifest)
  try:
   updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0]))); first=int(sim.meta['firstReceivedMs']);old.base.v2.base.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
   for ordinal,u in enumerate(updates):
    t=int(u[1]);sim.set_phase(ordinal,t);old.base.v2.base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t);old.base.v2.base.apply(sim.book,u);qv=old.base.v2.base.quotes(sim.book)
    if qv:sim._risk_contract_if_needed(t);sim._reanchor_stale(t)
    if ordinal==int(seam['phaseOrdinal']):
     ok=sim._freeze_fork(ordinal,t,qv,end);print(json.dumps({'ok':ok,'checks':sim.forkSnapshot['checks'],'reference':sim.forkSnapshot['reference'],'manifest':manifest,'liveR0':sim.forkSnapshot['R0']},ensure_ascii=False,indent=2));break
    if qv:sim._open_one_option(t,qv,end)
    sim._sample_occupancy()
  finally:sim.close()
if __name__=='__main__':main()
