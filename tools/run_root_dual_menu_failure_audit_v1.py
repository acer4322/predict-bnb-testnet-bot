"""One-market diagnostic continuation of a NOT_EXERCISED smoke, no policy fork."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,'C:/BTC5M-worker/.tmp/hftbacktest_244')
from tools import run_root_dual_legal_label_smoke_v1 as lab
from tools import run_root_btc5m_source_smoke_v1 as util


def main():
    assert Path.cwd().resolve()==ROOT
    manifest=json.loads((ROOT/'AUDIT_MANIFEST.json').read_text())
    for p,h in manifest.items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    base=importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle').BoundedCoreServiceFavorableRecycleSim
    output=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    class Audit(base):
        def __init__(self,tape):
            self._lab={'t':None,'reasons':Counter(),'samples':{},'checks':0}
            super().__init__(tape,1,4)
        def _open_one_option(self,t,qv,end):
            self._lab['t']=t
            return super()._open_one_option(t,qv,end)
        def _role_decision(self,qv):
            decision=super()._role_decision(qv)
            if decision is not None and decision[1] in {'SATELLITE_REPAIR','SATELLITE_EXPAND'}:
                before=lab.signature(self); diag={}
                menu=lab.complete_menu(self,self._lab['t'],diag)
                assert lab.signature(self)==before
                reason=diag.get('reason','DUAL_LEGAL')
                self._lab['reasons'][reason]+=1;self._lab['checks']+=1
                if reason not in self._lab['samples']:
                    self._lab['samples'][reason]={'t':self._lab['t'],'nativeDecision':decision,
                        'diagnostic':diag,'state':util.state(self),'book':self.book,
                        'claims':[{'slot':sid,'key':k,'role':role,'generation':self.key_scope_gen.get(k),
                                   'remaining':self._remaining(k),'cancelPending':bool(o.get('cancelRequested'))}
                                  for sid,k,o,role in self._live_role_rows()]}
                    # Freeze values now; do not retain references to later-mutating state.
                    self._lab['samples'][reason]=util.clean(self._lab['samples'][reason])
            return decision
    (output/'BE_ACCOUNTING.json').write_text(json.dumps({'attemptedBE':1,'marketId':2022527,'diagnosticOnly':True}),encoding='utf-8')
    sim=Audit(ROOT/'tapes/2022527.json.xz')
    try:
        result=sim.run_r247('UP')
        sig=lab.signature(sim,result)
        expected='aea9c1cca872fc4c01f621a5f74a1901205d5982454d82f6edaad48d5c02204b'
        assert sig==expected,'diagnostic changed native behavior'
        out={'version':'ROOT_DUAL_MENU_FAILURE_AUDIT_V1','marketId':2022527,'attemptedBE':1,
             'baselineParity':True,'checks':sim._lab['checks'],'reasons':sim._lab['reasons'],
             'firstReasonWitnesses':sim._lab['samples'],'nativeCash':util.cash_check(sim.inv,sim.cost,util.native_state(sim)),
             'policyChanges':0,'modelsTrained':0,'targetInputs':False}
        blob=json.dumps(util.clean(out),indent=2).encode();assert len(blob)<=64*1024
        (output/'COMPACT.json').write_bytes(blob)
        print(json.dumps({'baselineParity':True,'checks':out['checks'],'reasons':out['reasons']}),flush=True)
    finally:sim.close()


if __name__=='__main__':main()
