from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
EPS=1e-9

class CoreFailureEvidenceRoutingSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.sourceRoles=set(self.sourceRoles)|{'ECONOMIC_CORE'}
    def run_r242(self,winner):
        r=super().run_cap(winner)
        r['r242SourceRoles']=sorted(self.sourceRoles)
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r242_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[];cmp=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            bsim=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=bsim.run_cap(cr['winner'])
            finally:bsim.close()
            csim=CoreFailureEvidenceRoutingSim(tape,1,4)
            try:c=csim.run_r242(cr['winner'])
            finally:csim.close()
            rows.extend([{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},{'marketId':mid,'cell':'MS4_R242_CORE_FAILURE_EVIDENCE','winnerPostHocOnly':cr['winner'],**c}])
            st=c.get('failureEvidenceActiveDrainStats',{})
            d={'marketId':mid,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],
               'fillDelta':c['fillEvents']-b['fillEvents'],'submitDelta':c['submits']-b['submits'],
               'genuineZeroFillEvidence':int(st.get('GENUINE_ZERO_FILL_EVIDENCE',0)),'activeDrainSubmits':int(st.get('ACTIVE_DRAIN_SUBMIT',0)),
               'residualBelowActiveMin':int(st.get('RESIDUAL_DEBT_BELOW_ACTIVE_MIN',0)),'staleDrops':int(st.get('STALE_EVIDENCE_DROP',0)),
               'unauthorizedOverflowQty':c.get('unauthorizedOverflowQty',0.0),'repairQuotaExcessMax':c.get('repairQuotaExcessMax',0.0)}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        cand={r['marketId']:r for r in rows if r['cell']=='MS4_R242_CORE_FAILURE_EVIDENCE'}
        out={'version':'MS4_R2_42_CORE_FAILURE_EVIDENCE_ROUTING_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(float(cand[m].get('unauthorizedOverflowQty',0.0))<=EPS and float(cand[m].get('repairQuotaExcessMax',0.0))<=EPS for m in mids),
                      'coreEvidenceExercised':any(x['genuineZeroFillEvidence']>0 for x in cmp)},
             'boundary':['CAP1 frozen except ECONOMIC_CORE is added to failure-evidence sourceRoles','Active remains pure venue-min Repair at current ask','no overflow/new debt/new risk authority added','winner post-hoc only','realistic HFT','no dream fill','<=180s unchanged','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
