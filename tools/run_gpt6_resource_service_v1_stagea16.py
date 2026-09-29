from __future__ import annotations
import json,tempfile,zipfile,time,math
from pathlib import Path

from tools import run_gpt6_three_failure_resource_service_v1 as ext
from tools.gpt6_three_failure_resource_service_v1 import ResourceServiceSim

ROOT=Path(__file__).resolve().parents[1]
PR=ROOT/'data/research/r4_v0/p0_provenance_v1/GPT6_RESOURCE_SERVICE_V1_STAGEA16_PREREGISTERED_20260906.json'
BENCH=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R240_CANONICAL_FULL24_BENCHMARK_20260906.json'
BUNDLE=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_latest_settled24_20260905_bundle.zip'
OUT=ROOT/'data/research/r4_v0/gpt6_resource_service_v1_external/stagea16_result.json'
EPS=1e-9

def main():
    if OUT.exists(): raise FileExistsError(OUT)
    prereg=json.loads(PR.read_text(encoding='utf-8')); mids=[int(x) for x in prereg['marketIds']]
    if len(mids)!=16 or len(set(mids))!=16: raise ValueError('StageA16 prereg invalid')
    pins=ext.source_pins(); pins['tapeBundle']=ext.digest(BUNDLE)
    bench=json.loads(BENCH.read_text(encoding='utf-8')); bm={int(x['marketId']):x for x in bench['markets']}
    rows=[]; cmp=[]; total_start=time.time()
    with zipfile.ZipFile(BUNDLE) as z:
        cohort={int(r['marketId']):r for r in json.loads(z.read('cohort.json'))['rows']}
        for mid in mids:
            t0=time.time(); cr=cohort[mid]
            with tempfile.TemporaryDirectory(prefix=f'gpt6_stagea_{mid}_') as td:
                tape=Path(td)/f'{mid}.json.xz'; tape.write_bytes(z.read(f'tapes/{mid}.json.xz'))
                sim=ResourceServiceSim(tape,1,4)
                try:r=sim.run_candidate(cr['winner'])
                finally:sim.close()
            row={**r,'marketId':mid,'cell':'GPT6_CANDIDATE'}; ext.validate(row); rows.append(row)
            base=bm[mid]['R240_CONTROL']; cap=bm[mid]['CAP1_CONTROL'];
            d={'marketId':mid,'originalCap1Outcome':bm[mid]['originalCap1Outcome'],
               'mechanismFamily':(bm[mid].get('failureAttribution') or {}).get('mechanismFamily'),
               'pnl':float(r['pnlDiagnosticOnly']),'r240Pnl':float(base['pnl']),'pnlDeltaVsR240':float(r['pnlDiagnosticOnly'])-float(base['pnl']),
               'floor':float(r['floor']),'r240Floor':float(base['floor']),'floorDeltaVsR240':float(r['floor'])-float(base['floor']),
               'best':float(r['best']),'bestDeltaVsR240':float(r['best'])-float(base['best']),
               'fills':int(r['fillEvents']),'r240Fills':int(base['fillEvents']),'submits':int(r['submits']),'r240Submits':int(base['submits']),
               'candidateCorrectnessPass':bool(r['candidateCorrectnessPass']),'candidateInterventions':r['candidateInterventions'],
               'runtimeSeconds':time.time()-t0}
            cmp.append(d); print(json.dumps(d,ensure_ascii=False),flush=True)
    c_pnl=sum(x['pnl'] for x in cmp); b_pnl=sum(x['r240Pnl'] for x in cmp); c_floor=sum(x['floor'] for x in cmp); b_floor=sum(x['r240Floor'] for x in cmp)
    c_fills=sum(x['fills'] for x in cmp); b_fills=sum(x['r240Fills'] for x in cmp); c_sub=sum(x['submits'] for x in cmp); b_sub=sum(x['r240Submits'] for x in cmp)
    wins=[x for x in cmp if x['originalCap1Outcome']=='WIN']
    winner_pnl_delta=sum(x['pnlDeltaVsR240'] for x in wins); winner_floor_delta=sum(x['floorDeltaVsR240'] for x in wins)
    correctness=all(x['candidateCorrectnessPass'] for x in cmp)
    gates={'all16Complete':len(rows)==16,'candidateCorrectnessAllZero':correctness,
           'aggregatePnlNotWorseThanR240':c_pnl+EPS>=b_pnl,'aggregateFloorNotWorseThanR240':c_floor+EPS>=b_floor,
           'originalWinnerAggregatePnlNotWorse':winner_pnl_delta>=-EPS,'originalWinnerAggregateFloorNotWorse':winner_floor_delta>=-EPS,
           'fillRetentionVsR240':c_fills/b_fills if b_fills else None,'submitRetentionVsR240':c_sub/b_sub if b_sub else None,
           'antiCollapse50pctPass':(not b_fills or c_fills/b_fills>=0.5) and (not b_sub or c_sub/b_sub>=0.5)}
    gates['stageAPass']=all(v is True for k,v in gates.items() if k not in ('fillRetentionVsR240','submitRetentionVsR240'))
    out={'version':'GPT6_RESOURCE_SERVICE_V1_STAGEA16_RESULT','researchOnly':True,'runtimeAuthority':False,'provenance':pins,'preregistration':str(PR.relative_to(ROOT)),
         'markets':mids,'rows':rows,'comparison':cmp,
         'aggregate':{'candidatePnl':c_pnl,'r240Pnl':b_pnl,'pnlDeltaVsR240':c_pnl-b_pnl,'candidateFloor':c_floor,'r240Floor':b_floor,'floorDeltaVsR240':c_floor-b_floor,
                      'candidateFills':c_fills,'r240Fills':b_fills,'candidateSubmits':c_sub,'r240Submits':b_sub,'winnerPnlDeltaVsR240':winner_pnl_delta,'winnerFloorDeltaVsR240':winner_floor_delta,
                      'totalRuntimeSeconds':time.time()-total_start,'maxMarketRuntimeSeconds':max(x['runtimeSeconds'] for x in cmp)},
         'gates':gates,'promotionAuthorized':False}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'ok':True,'gates':gates,'aggregate':out['aggregate'],'output':str(OUT.relative_to(ROOT))},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
