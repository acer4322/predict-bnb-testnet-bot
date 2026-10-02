from __future__ import annotations
import argparse,json,tempfile,zipfile,time
from pathlib import Path
from tools import run_gpt6_three_failure_resource_service_v1 as ext
from tools.gpt6_three_failure_resource_service_v1 import ResourceServiceSim
ROOT=Path(__file__).resolve().parents[1]
BENCH=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R240_CANONICAL_FULL24_BENCHMARK_20260906.json'
BUNDLE=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_latest_settled24_20260905_bundle.zip'
PR=ROOT/'data/research/r4_v0/p0_provenance_v1/GPT6_RESOURCE_SERVICE_V1_STAGEA16_PREREGISTERED_20260906.json'
EPS=1e-9

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; allowed=set(json.loads(PR.read_text(encoding='utf-8'))['marketIds'])
 if not mids or len(mids)>8 or len(set(mids))!=len(mids) or any(m not in allowed for m in mids): raise ValueError('batch must be unique prereg Stage-A markets, max8')
 outp=ROOT/a.output
 if outp.exists(): raise FileExistsError(outp)
 pins=ext.source_pins(); pins['tapeBundle']=ext.digest(BUNDLE)
 bench=json.loads(BENCH.read_text(encoding='utf-8')); bm={int(x['marketId']):x for x in bench['markets']}; rows=[]; cmp=[]; t_all=time.time()
 with zipfile.ZipFile(BUNDLE) as z:
  cohort={int(r['marketId']):r for r in json.loads(z.read('cohort.json'))['rows']}
  for mid in mids:
   t0=time.time(); cr=cohort[mid]
   with tempfile.TemporaryDirectory(prefix=f'gpt6_stagea_{mid}_') as td:
    tape=Path(td)/f'{mid}.json.xz'; tape.write_bytes(z.read(f'tapes/{mid}.json.xz'))
    sim=ResourceServiceSim(tape,1,4)
    try:r=sim.run_candidate(cr['winner'])
    finally:sim.close()
   row={**r,'marketId':mid,'cell':'GPT6_CANDIDATE'}; ext.validate(row); rows.append(row); base=bm[mid]['R240_CONTROL']
   d={'marketId':mid,'originalCap1Outcome':bm[mid]['originalCap1Outcome'],'mechanismFamily':(bm[mid].get('failureAttribution') or {}).get('mechanismFamily'),
      'pnl':float(r['pnlDiagnosticOnly']),'r240Pnl':float(base['pnl']),'pnlDeltaVsR240':float(r['pnlDiagnosticOnly'])-float(base['pnl']),
      'floor':float(r['floor']),'r240Floor':float(base['floor']),'floorDeltaVsR240':float(r['floor'])-float(base['floor']),
      'bestDeltaVsR240':float(r['best'])-float(base['best']),'fills':int(r['fillEvents']),'r240Fills':int(base['fillEvents']),'submits':int(r['submits']),'r240Submits':int(base['submits']),
      'candidateCorrectnessPass':bool(r['candidateCorrectnessPass']),'candidateInterventions':r['candidateInterventions'],'runtimeSeconds':time.time()-t0}
   cmp.append(d); print(json.dumps(d,ensure_ascii=False),flush=True)
 cpn=sum(x['pnl'] for x in cmp); bpn=sum(x['r240Pnl'] for x in cmp); cfl=sum(x['floor'] for x in cmp); bfl=sum(x['r240Floor'] for x in cmp); cf=sum(x['fills'] for x in cmp); bf=sum(x['r240Fills'] for x in cmp); cs=sum(x['submits'] for x in cmp); bs=sum(x['r240Submits'] for x in cmp)
 result={'version':'GPT6_RESOURCE_SERVICE_V1_STAGEA_BATCH','researchOnly':True,'runtimeAuthority':False,'provenance':pins,'markets':mids,'rows':rows,'comparison':cmp,
  'aggregate':{'candidatePnl':cpn,'r240Pnl':bpn,'pnlDeltaVsR240':cpn-bpn,'candidateFloor':cfl,'r240Floor':bfl,'floorDeltaVsR240':cfl-bfl,'fillRetention':cf/bf if bf else None,'submitRetention':cs/bs if bs else None,'runtimeSeconds':time.time()-t_all},
  'allCorrect':all(x['candidateCorrectnessPass'] for x in cmp)}
 outp.parent.mkdir(parents=True,exist_ok=True); outp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8'); print(json.dumps({'ok':True,'aggregate':result['aggregate'],'allCorrect':result['allCorrect'],'output':a.output},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
