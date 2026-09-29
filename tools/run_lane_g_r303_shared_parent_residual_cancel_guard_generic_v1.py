from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_r303_shared_parent_residual_cancel_guard_v1.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_r303_shared_parent_residual_cancel_guard_v1 as rg
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_r303_shared_parent_residual_cancel_guard_v1 as rg
MODES=['INHERITED_SCOPE_CONTRACT_CANCEL','KEEP_SHARED_PARENT_RESIDUAL']

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    spec_doc=json.loads(Path(a.spec).read_text(encoding='utf-8'));specs={int(k):v for k,v in spec_doc['markets'].items()};mids=sorted(specs)
    for m,s in specs.items():rg.ma.FROZEN[m]=s
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_residual_generic_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            if m not in cohort:raise RuntimeError(f'market {m} absent from cohort')
            for mode in MODES:
                sim=rg.ResidualCancelGuardSim(tmp/f'{m}.json.xz',m,mode)
                try:r=sim.run_guard(cohort[m]['winner'])
                finally:sim.close()
                rows.append(r)
                print(json.dumps({'marketId':m,'mode':mode,'triggered':r['residualDecisionTriggered'],'resolved':r['residualDecisionResolved'],'trigger':r.get('residualTrigger'),'event':r.get('residualEvent'),'terminal':r['terminal'],'correct':r['residualCorrect'],'errors':r['triggerParityErrors']},ensure_ascii=False),flush=True)
        vectors=[];parity={}
        for m in mids:
            rr=[x for x in rows if int(x['marketId'])==m];c=next(x for x in rr if x['residualMode']=='INHERITED_SCOPE_CONTRACT_CANCEL');k=next(x for x in rr if x['residualMode']=='KEEP_SHARED_PARENT_RESIDUAL')
            cs=(c.get('residualTrigger') or {}).get('state');ks=(k.get('residualTrigger') or {}).get('state');parity[str(m)]=(cs==ks if cs is not None or ks is not None else True)
            for x in rr:
                ev=(x.get('residualEvent') or {}).get('state') or {};ctrl=(c.get('residualEvent') or {}).get('state') or {}
                ep=ev.get('payoff');cp=ctrl.get('payoff');par=ev.get('parent') or {}
                vec={'marketId':m,'mode':x['residualMode'],'triggered':x['residualDecisionTriggered'],'resolved':x['residualDecisionResolved'],'eventT':(x.get('residualEvent') or {}).get('t'),'eventReasons':(x.get('residualEvent') or {}).get('reasons'),'correct':x['residualCorrect'],'parentRemainingDebt':par.get('remainingDebt'),'confirmedRepairPaid':par.get('repairPaid'),'overflowQty':ev.get('r303OverflowQty'),'overflowRisk':ev.get('r303OverflowRisk')}
                if ep and cp:vec.update({'deltaBestVsCancel':float(ep['best'])-float(cp['best']),'deltaFloorVsCancel':float(ep['floor'])-float(cp['floor']),'deltaGapVsCancel':float(ep['gap'])-float(cp['gap'])})
                vectors.append(vec)
        gates={'allDecisionTriggers':all(x['residualDecisionTriggered'] for x in rows),'allLocalStopsResolved':all(x['residualDecisionResolved'] for x in rows if x['residualDecisionTriggered']),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['residualCorrect'] for x in rows if x['residualDecisionTriggered']) if any(x['residualDecisionTriggered'] for x in rows) else False}
        out={'version':'LANE_G_R303_SHARED_PARENT_RESIDUAL_CANCEL_GUARD_GENERIC_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'sourceSpec':a.spec,'markets':mids,'rows':rows,'vectors':vectors,'gates':gates,'triggerParityByMarket':parity,'boundary':['external prereg spec only','same R303 shared parent and one-unit authority','no extra risk/credit/qty/slot','manager frozen only from cancel/keep decision until next shared structural event','structural-event horizon only','Active unchanged','consumed only','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'vectors':vectors},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
