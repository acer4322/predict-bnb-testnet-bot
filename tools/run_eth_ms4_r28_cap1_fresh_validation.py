from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1; EPS=1e-9

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r28_fresh_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=co[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r1.QueueAwareRepairRoutingSim(tape,4)
            try: base=ctl.run_v88(cr['winner'])
            finally: ctl.close()
            sim=r28.FanoutRoleCapacitySim(tape,1,4)
            try: cand=sim.run_cap(cr['winner'])
            finally: sim.close()
            rows += [
                {'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**base},
                {'marketId':mid,'cell':'MS4_R28_CAP1_FROZEN','winnerPostHocOnly':cr['winner'],**cand},
            ]
            print(json.dumps({'marketId':mid,'r1':{'submits':base['submits'],'fills':base['fillEvents'],'pnl':base['pnlDiagnosticOnly'],'floor':base['floor']},'cap1':{'submits':cand['submits'],'fills':cand['fillEvents'],'pnl':cand['pnlDiagnosticOnly'],'floor':cand['floor'],'fan':cand['parallelPassiveFanoutSubmits'],'fanFill':cand['parallelPassiveFanoutFilledKeys'],'active':cand.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'unauth':cand['unauthorizedOverflowQty'],'quotaExcess':cand['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'}; C={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_FROZEN'}
        cmp=[]
        for m in mids:
            b,c=B[m],C[m]
            cmp.append({'marketId':m,'submitDelta':c['submits']-b['submits'],'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'fanoutSubmits':c['parallelPassiveFanoutSubmits'],'fanoutFilledKeys':c['parallelPassiveFanoutFilledKeys']})
        baseF=sum(B[m]['fillEvents'] for m in mids); candF=sum(C[m]['fillEvents'] for m in mids)
        baseP=sum(B[m]['pnlDiagnosticOnly'] for m in mids); candP=sum(C[m]['pnlDiagnosticOnly'] for m in mids)
        baseFloor=sum(B[m]['floor'] for m in mids); candFloor=sum(C[m]['floor'] for m in mids)
        correctness=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids)
        anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        exercised=sum(C[m].get('parallelPassiveFanoutSubmits',0) for m in mids)>0
        gates={'correctnessPass':correctness,'aggregateFillNonRegressionPass':candF>=baseF,'antiCollapse50pctPass':anti,'aggregatePnlNonRegressionPass':candP+EPS>=baseP,'aggregateFloorNonRegressionPass':candFloor+EPS>=baseFloor,'fanoutExercisedPass':exercised}
        gates['promotionPass']=all(gates.values())
        out={'version':'MS4_R28_CAP1_FRESH_VALIDATION_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'aggregate':{'r1':{'submits':sum(B[m]['submits'] for m in mids),'fills':baseF,'pnl':baseP,'floor':baseFloor},'cap1':{'submits':sum(C[m]['submits'] for m in mids),'fills':candF,'pnl':candP,'floor':candFloor,'fanoutSubmits':sum(C[m].get('parallelPassiveFanoutSubmits',0) for m in mids),'fanoutFilledKeys':sum(C[m].get('parallelPassiveFanoutFilledKeys',0) for m in mids)}},'gates':gates,'boundary':['CAP1 frozen before Fresh8D','max_slots remains 4','max one concurrent extra parallel Repair satellite; unused slots remain available to Core/Expand/continuation','Repair accounting/Overflow/Active authority unchanged','cohort selected by chronology+settled+tape availability only','winner post-hoc scoring only','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':out['aggregate'],'gates':gates,'comparison':cmp},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
