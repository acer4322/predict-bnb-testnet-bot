from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_multi_action_exact_fork_v1b as ma
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    spec_doc=json.loads(Path(a.spec).read_text(encoding='utf-8'));specs={int(k):v for k,v in spec_doc['markets'].items()};mids=sorted(specs)
    for m,s in specs.items():ma.FROZEN[m]=s
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_ma_generic_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            if m not in cohort:raise RuntimeError(f'market {m} absent from cohort')
            for action in ['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']:
                sim=ma.MultiActionExactForkSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:r=sim.run_exact(cohort[m]['winner'])
                finally:sim.close()
                rows.append(r)
                print(json.dumps({'marketId':m,'action':action,'triggered':r['triggered'],'resolved':r['resolved'],'event':(r.get('firstStructuralEvent') or {}).get('reasons'),'postEventFirstManagerAction':r.get('postEventFirstManagerAction'),'terminal':r['terminal'],'correct':r['correct'],'errors':r['triggerParityErrors']},ensure_ascii=False),flush=True)
        parity={};vectors=[]
        for m in mids:
            rr=[x for x in rows if int(x['marketId'])==m];base=next(x for x in rr if x['action']=='KEEP_REPAIR');bstate=(base.get('trigger') or {}).get('state')
            parity[str(m)]=all((x.get('trigger') or {}).get('state')==bstate for x in rr)
            bp=(base.get('firstStructuralEvent') or {}).get('payoff')
            for x in rr:
                ev=x.get('firstStructuralEvent') or {};ep=ev.get('payoff');vec={'marketId':m,'action':x['action'],'eventT':ev.get('t'),'eventReasons':ev.get('reasons'),'correct':x['correct'],'postEventFirstManagerAction':x.get('postEventFirstManagerAction')}
                if ep is not None and bp is not None:
                    vec.update({'deltaBestVsKeepEvent':float(ep['best'])-float(bp['best']),'deltaFloorVsKeepEvent':float(ep['floor'])-float(bp['floor']),'deltaGapVsKeepEvent':float(ep['gap'])-float(bp['gap'])})
                vec.update({'responsibilityPaymentProgress':(ev.get('obligation') or {}).get('repaidQty'),'confirmedNewRiskOverflowQty':float(ev.get('r303OverflowQty') or 0.0),'confirmedNewRiskOverflowRisk':float(ev.get('r303OverflowRisk') or 0.0),'activeUsageCost':float(ev.get('activeFillQtyDelta') or 0.0)})
                vectors.append(vec)
        gates={'allTriggered':all(x['triggered'] for x in rows),'allResolvedByStructuralEvent':all(x['resolved'] for x in rows),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['correct'] for x in rows)}
        out={'version':'LANE_G_MULTI_ACTION_EXACT_FORK_GENERIC_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'sourceSpec':a.spec,'markets':mids,'rows':rows,'actionVectors':vectors,'gates':gates,'triggerParityByMarket':parity,'boundary':['external prereg spec only','exact R2.64 behavior before trigger','branch differs only in same one-unit authority representation','manager frozen only until first structural event','structural-event horizon only','same realistic HFT tape/latency/queue','max4 and <=180s unchanged','no Target/winner future branch input','consumed only','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'vectors':vectors},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
