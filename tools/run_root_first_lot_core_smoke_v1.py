"""One original-lot Core commitment; not repeated aggregate-debt clearing."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import math

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,'C:/BTC5M-worker/.tmp/hftbacktest_244')
from tools import run_root_dual_legal_label_smoke_v1 as lab
from tools import run_root_btc5m_source_smoke_v1 as util


def eligible_quantity(native_qty, lot_qty):
    return math.isfinite(lot_qty) and math.isfinite(native_qty) and 0 < native_qty < lot_qty-1e-9 and lot_qty<=12+1e-9


def main():
    assert Path.cwd().resolve()==ROOT
    m=json.loads((ROOT/'LOT_MANIFEST.json').read_text())
    for p,h in m.items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    prior=json.loads((ROOT/'prior.json').read_text())
    refs={r['marketId']:r for r in prior['rows'] if r['branch']=='N'}
    base=importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle').BoundedCoreServiceFavorableRecycleSim
    output=Path(os.environ['BTC5M_LAN_RESULT_DIR']);rows=[];be=0;start=time.time()
    class Lot(base):
        def __init__(self,tape,branch):
            self._lab={'branch':branch,'witness':None,'actual':None,'t':None,'obs':util.Observer([])}
            super().__init__(tape,1,4)
        def process(self,t):
            r=super().process(t);self._lab['obs'].post_process(self,t);return r
        def _open_one_option(self,t,qv,end):
            self._lab['t']=t;return super()._open_one_option(t,qv,end)
        def _candidate_from_levels_v8(self,side,role,require_pair):
            candidate=super()._candidate_from_levels_v8(side,role,require_pair)
            d=self._lab
            if candidate is None or role!='ECONOMIC_CORE' or d['witness'] is not None or self.scopeSide is None:
                return candidate
            lots=list(self.un[self.scopeSide])
            if len(lots)!=1 or side!=self._repair_side() or self._reserved_repair_quota()>1e-9:
                return candidate
            p,q,proj,split=candidate;whole=float(lots[0][0])
            if not eligible_quantity(q,whole):return candidate
            before=lab.signature(self);c=lab.preview_copy(self);sp=c._repair_split(side,p,whole)
            assert lab.signature(self)==before
            if sp is None or abs(sp['overflowQty'])>1e-9 or abs(sp['repairQty']-whole)>1e-9:return candidate
            d['witness']={'t':d['t'],'prefixSignature':before,'side':side,'price':p,'nativeQty':q,'lotQty':whole,
                          'lotAcquisitionPrice':float(lots[0][1]),'scopeGeneration':self.scopeGeneration,
                          'split':sp,'state':util.clean(util.state(self))}
            return (p,whole,sp['fullFloor'],sp) if d['branch']=='Q' else candidate
        def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
            value=super()._submit_role_v8(t,side,role,p,q,proj,split)
            d=self._lab;w=d['witness']
            if w is not None and d['actual'] is None and role=='ECONOMIC_CORE':
                expected=w['lotQty'] if d['branch']=='Q' else w['nativeQty']
                assert value and side==w['side'] and p==w['price'] and abs(q-expected)<=1e-8
                d['actual']={'t':t,'q':q,'p':p,'accepted':True,'key':f'{side}_{self.n-1}'}
            return value
    try:
        for mid in [2022527,2022538,2022602]:
            for branch in ['S','Q']:
                be+=1;(output/'BE_ACCOUNTING.json').write_text(json.dumps({'attemptedBE':be,'marketId':mid,'branch':branch}),encoding='utf-8')
                sim=Lot(ROOT/f'tapes/{mid}.json.xz',branch)
                try:
                    r=sim.run_r247('UP');d=sim._lab;obs=d['obs'];sig=lab.signature(sim,r)
                    ck=util.cash_check(sim.inv,sim.cost,util.native_state(sim))
                    correct=ck['pass'] and all(v<=1e-8 for v in obs.max_error.values()) and r['r247ServiceCorrectnessPass'] and r['unauthorizedOverflowQty']<=1e-9 and r['repairQuotaExcessMax']<=1e-9 and sim.max_simultaneous_slots<=4
                    row={'marketId':mid,'branch':branch,'signature':sig,'correct':correct,'cashCheck':ck,'cashMaxError':obs.max_error,
                         'witness':d['witness'],'actual':d['actual'],'UP':sim.inv['UP']-sim.cost,'DOWN':sim.inv['DOWN']-sim.cost,
                         'floor':r['floor'],'best':r['best'],'cost':sim.cost,'fills':sim.fills,'qty':sum(sim.inv.values()),
                         'submits':sim.submits,'scopeCompletions':sim.scopeCompletions,'scopeFlips':sim.scopeFlips,
                         'peakAbsNet':obs.peak_abs_net,'netIntegralShareMs':obs.abs_net_integral_ms,
                         'activeMeta':util.clean(sim.activeMeta),'fillHistory':util.clean(sim.fillHist),'orders':util.clean(sim.orders)}
                    rows.append(row);assert correct,'cash/substrate correctness'
                    if branch=='S':assert sig==refs[mid]['signature'],'S/N parity'
                    else:assert util.fingerprint(row['witness'])==util.fingerprint(rows[-2]['witness']),'S/Q prefix'
                    print(json.dumps({'market':mid,'branch':branch,'witness':bool(d['witness']),'UP':row['UP'],'DOWN':row['DOWN'],'fills':row['fills']}),flush=True)
                finally:sim.close()
        qs=[r for r in rows if r['branch']=='Q']; comps=[]
        for q in qs:
            n=refs[q['marketId']]
            comps.append({'marketId':q['marketId'],'delta':{k:q[k]-n[k] for k in ['UP','DOWN','floor','best','cost','fills','qty','scopeCompletions','peakAbsNet','netIntegralShareMs']},
                          'antiCollapse':all(n[k]<=0 or q[k]>=.5*n[k] for k in ['fills','qty','scopeCompletions']),
                          'exercised':bool(q['actual'])})
        anti=all(c['antiCollapse'] for c in comps);ex=sum(c['exercised'] for c in comps)>=2
        econ=(all(sum(c['delta'][k] for c in comps)>=-1e-8 for k in ['UP','DOWN','best']) and
              min(q['floor'] for q in qs)>=min(n['floor'] for n in refs.values())-1e-8 and
              all(c['delta']['peakAbsNet']<=1e-8 for c in comps))
        verdict='NOT_EXERCISED' if not ex else 'ACTIVITY_COLLAPSE' if not anti else 'ECONOMIC_TRADEOFF_REJECT' if not econ else 'FIRST_LOT_COMMITMENT_SUPPORTED_IN_SMOKE'
        result={'verdict':verdict,'rows':rows,'comparison':comps,'gates':{'exercise':ex,'antiCollapse':anti,'economics':econ}}
    except Exception as e:
        result={'verdict':'CORRECTNESS_STOP','error':type(e).__name__+': '+str(e),'rows':rows}
    result.update({'attemptedBE':be,'elapsedSeconds':time.time()-start,'modelsTrained':0,'freshUsed':0,'fullNetPnl':'UNRESOLVED','targetInputs':False})
    blob=json.dumps(util.clean(result),indent=2).encode();assert len(blob)<=512*1024
    (output/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP':raise SystemExit(2)


if __name__=='__main__':main()
