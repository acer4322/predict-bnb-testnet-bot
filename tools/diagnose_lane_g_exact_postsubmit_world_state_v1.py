from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_multi_action_exact_fork_v1b as ma;import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma
    from tools import train_lane_g_r264_execution_world_v1 as wm

ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']
class SnapSim(ma.MultiActionExactForkSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.worldPostSubmit=None;self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _arm_fork(self,t,branch_key=None,pre_state=None):
        super()._arm_fork(t,branch_key,pre_state)
        s=self.spec
        if self.action=='KEEP_REPAIR':
            key=str(s['siblingKey']);o=self.orders[key];side=str(o['side']);role=str(self.key_role.get(key) or 'SATELLITE_REPAIR');price=float(o['price']);qty=float(self._remaining(key));source='EXISTING_REPAIR_SIBLING'
        elif self.action=='ORDINARY_REEXPAND':
            key=str(branch_key);o=self.orders[key];side=str(o['side']);role=str(self.key_role.get(key) or 'SATELLITE_EXPAND');price=float(o['price']);qty=float(o['qty']);source='EXACT_ORDINARY_POSTSUBMIT'
        else:
            key=str(branch_key);o=self.orders[key];side=str(o['side']);role=str(self.key_role.get(key) or 'SATELLITE_REPAIR');price=float(o['price']);qty=float(o['qty']);source='EXACT_R303_POSTSUBMIT'
        self.worldPostSubmit=wm.TraceSim._state_row(self,int(t),side,role,price,qty,'PASSIVE',key,source)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    spec_doc=json.loads(Path(a.spec).read_text(encoding='utf-8'));specs={int(k):v for k,v in spec_doc['markets'].items()};mids=sorted(specs)
    for m,s in specs.items():ma.FROZEN[m]=s
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_postsubmit_state_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for action in ACTIONS:
                sim=SnapSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:r=sim.run_exact(co[m]['winner']);wr=sim.worldPostSubmit
                finally:sim.close()
                rows.append({'marketId':m,'action':action,'triggered':r['triggered'],'triggerParityErrors':r['triggerParityErrors'],'rawCorrect':r['correct'],'worldPostSubmit':wr})
                print(json.dumps({'marketId':m,'action':action,'triggered':r['triggered'],'rawCorrect':r['correct'],'liveSlots':wr.get('liveSlots') if wr else None,'repairLiveSlots':wr.get('repairLiveSlots') if wr else None,'expandLiveSlots':wr.get('expandLiveSlots') if wr else None,'representedRepairQuota':wr.get('representedRepairQuota') if wr else None,'availableExpandCredit':wr.get('availableExpandCredit') if wr else None},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_EXACT_POSTSUBMIT_WORLD_STATE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'gates':{'allTriggered':all(x['triggered'] for x in rows),'triggerParityClean':all(not x['triggerParityErrors'] for x in rows),'allSnapshotsPresent':all(x['worldPostSubmit'] is not None for x in rows)},'boundary':['diagnostic only','world state sampled immediately after exact branch materialization at frozen trigger','same exact R2.64/R303 fork semantics','no strategy behavior mutation','consumed only','no fresh/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
