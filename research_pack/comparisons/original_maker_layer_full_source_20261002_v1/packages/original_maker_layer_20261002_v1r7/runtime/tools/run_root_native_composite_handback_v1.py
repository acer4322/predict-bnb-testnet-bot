"""One predeclared native Core-service handback, unchanged native suffix."""
import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_root_pre_active_option_audit_v1 as audit
lab, util = audit.lab, audit.util


def is_repair_proposal(p, side):
    c = p.get('candidate') or {}; split = c.get('split') or {}
    return bool(p.get('transportReached') and c.get('side') == side and
                c.get('role') in {'ECONOMIC_CORE', 'SATELLITE_REPAIR'} and
                split.get('repairQty', 0) > 1e-9)


def make_fork(base, mod):
    class Fork(base):
        def __init__(self, tape, branch, source):
            self._lab = dict(branch=branch, observer=util.Observer(source), witness=None,
                             selectedActive=None, selectedPassive=None)
            super().__init__(tape, 1, 4)

        def process(self, t):
            r=super().process(t); self._lab['observer'].post_process(self,t); return r

        def _submit_active(self, t, side, role, q, score, diag):
            d=self._lab
            if d['branch']=='N' or not self._coreServiceContext or d['witness'] is not None:
                return super()._submit_active(t,side,role,q,score,diag)
            ev=self.pendingCoreEvidence; qv=mod.r1.v2.base.quotes(self.book)
            ap=qv.get(side,{}).get('ask') if qv else None
            valid=(ev and self.scopeSide is not None and int(ev['generation'])==int(self.scopeGeneration)
                and side==self._repair_side() and role=='SATELLITE_REPAIR' and ap is not None
                and math.isfinite(q) and 0<q<=12+1e-9 and abs(q*ap-1)<=1e-8
                and self._scope_debt_qty()-self._reserved_repair_quota(side)+1e-9>=q
                and self._candidate_alone_floor(side,ap,q)>self._physical_floor()+1e-9
                and ap*q<=self._available_core_service_authority()+1e-9
                and len(self.slot_key)+len(self.activeKeys)<self.max_slots)
            if not valid: return super()._submit_active(t,side,role,q,score,diag)
            sig=lab.signature(self); cash=util.native_state(self)
            end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
            p=audit.passive_probe(self,base,t,qv,end)
            assert lab.signature(self)==sig and util.native_state(self)==cash
            if not is_repair_proposal(p,side): return super()._submit_active(t,side,role,q,score,diag)
            ob=d['observer']; pub=util.latest_public(ob.public,ob.times,t)
            assert pub is None or pub['availableMs']<t
            d['witness']=dict(t=t,prefixSignature=sig,state=util.clean(util.state(self)),
                pendingCoreEvidence=copy.deepcopy(ev),public=pub,passive=p,
                active=dict(side=side,role=role,price=ap,qty=q))
            if d['branch']=='P':
                # Exactly one call declined; preserve evidence. No later Active suppression.
                return False
            key=f'{side}_{self.n}'; ok=super()._submit_active(t,side,role,q,score,diag)
            assert ok, 'selected native Active prechecks disagreed'
            rec=next(x for x in reversed(self.executionDecisions)
                     if x.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and x.get('key')==key)
            assert rec['submitRc']==0, 'native Active transport failure'
            d['selectedActive']=util.clean(rec)
            return ok

        def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
            d=self._lab; w=d['witness']
            capture=(d['branch']=='P' and w is not None and t==w['t'] and d['selectedPassive'] is None)
            key=f'{side}_{self.n}'
            if capture:
                expected=w['passive']['candidate']
                actual=dict(t=t,side=side,role=role,price=p,qty=q,projection=proj,split=split)
                assert util.fingerprint(actual)==util.fingerprint(expected), 'native handback proposal changed'
            ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
            if capture and ok:
                assert key not in self.activeMeta and key in self.orders
                d['selectedPassive']=dict(key=key,t=t,side=side,role=role,price=p,qty=q,split=copy.deepcopy(split))
            return ok

    return Fork


def main():
    assert Path.cwd().resolve() == ROOT
    assert not (ROOT/'.lan_worker_v1/staging').exists()
    m = json.loads((ROOT/'HANDBACK_MANIFEST.json').read_text())
    for rel, sha in m['files'].items():
        assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() == sha, rel
    sys.path.insert(0, 'C:/BTC5M-worker/.tmp/hftbacktest_244')
    mod = importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    base = mod.BoundedCoreServiceFavorableRecycleSim
    Fork = make_fork(base, mod)
    refs = {r['marketId']:r for r in json.loads((ROOT/'prior.json').read_text())['rows'] if r['branch']=='N'}
    public = json.loads((ROOT/'dual_public.json').read_text())
    output = Path(os.environ['BTC5M_LAN_RESULT_DIR']); rows=[]; be=0; start=time.time()

    try:
        for mid in m['markets']:
            for branch in ['S','P']:
                be+=1
                (output/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=be,marketId=mid,branch=branch)))
                sim=Fork(ROOT/f'tapes/{mid}.json.xz',branch,public[str(mid)])
                try:
                    r=sim.run_r247('UP'); d=sim._lab; ob=d['observer']; sig=lab.signature(sim,r)
                    correct=(util.cash_check(sim.inv,sim.cost,util.native_state(sim))['pass'] and
                        all(v<=1e-8 for v in ob.max_error.values()) and r['r247ServiceCorrectnessPass'] and
                        r['unauthorizedOverflowQty']<=1e-9 and r['repairQuotaExcessMax']<=1e-9 and sim.max_simultaneous_slots<=4)
                    if branch=='S': correct=correct and sig==refs[mid]['signature']
                    else:
                        correct=correct and util.fingerprint(d['witness'])==util.fingerprint(rows[-1]['witness'])
                        if d['witness'] is None: correct=correct and sig==refs[mid]['signature']
                    row=dict(marketId=mid,branch=branch,signature=sig,correct=bool(correct),
                        witness=d['witness'],selectedActive=d['selectedActive'],selectedPassive=d['selectedPassive'],
                        UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,floor=r['floor'],best=r['best'],
                        cost=sim.cost,fills=sim.fills,submits=sim.submits,qty=sum(sim.inv.values()),
                        scopeCompletions=sim.scopeCompletions,scopeFlips=sim.scopeFlips,
                        peakAbsNet=ob.peak_abs_net,netIntegralShareMs=ob.abs_net_integral_ms,
                        cashMaxError=ob.max_error,fillHistory=util.clean(sim.fillHist),orders=util.clean(sim.orders),
                        activeMeta=util.clean(sim.activeMeta),serviceLedger=util.clean(sim.serviceLedger))
                    rows.append(row)
                    assert correct, 'cash/accounting/whole-prefix/parity failure'
                    print(json.dumps({k:row[k] for k in ['marketId','branch','correct','UP','DOWN','fills','submits']}),flush=True)
                finally: sim.close()
        comparison=[]
        for s,p in zip(rows[::2],rows[1::2]):
            comparison.append(dict(marketId=s['marketId'],selected=s['witness'] is not None,
                exercised=bool(s['selectedActive'] and p['selectedPassive']),
                delta={k:p[k]-s[k] for k in ['UP','DOWN','floor','best','cost','fills','submits','qty','scopeCompletions','scopeFlips','peakAbsNet','netIntegralShareMs']}))
        ss=rows[::2]; ps=rows[1::2]
        anti=(all(s['fills']==0 or p['fills']>0 for s,p in zip(ss,ps)) and
              all(sum(p[k] for p in ps)>=.5*sum(s[k] for s in ss) for k in ['fills','qty']))
        selected=[c for c in comparison if c['selected']]
        response=any(abs(c['delta'][k])>1e-8 for c in selected for k in ['UP','DOWN'])
        verdict=('NOT_EXERCISED_SOURCE' if not selected else
                 'HANDOFF_NOT_EXERCISED' if not all(c['exercised'] for c in selected) else
                 'SOURCE_RESPONSE_WITH_ACTIVITY_COLLAPSE' if not anti else
                 'COMPLETE_BUNDLE_RESPONSE_IDENTIFIED_IN_SMOKE_ONLY' if response else
                 'NO_TERMINAL_RESPONSE_IN_SMOKE')
        result=dict(verdict=verdict,rows=rows,comparison=comparison,antiCollapseSmokeOnly=anti)
    except Exception as e:
        result=dict(verdict='CORRECTNESS_STOP',error=type(e).__name__+': '+str(e),rows=rows)
    result.update(attemptedBE=be,elapsedSeconds=time.time()-start,modelsTrained=0,freshUsed=0,
                  promotion=False,trainingReady=False,fullNetPnl='UNSCORED')
    blob=json.dumps(util.clean(result),indent=2).encode(); assert len(blob)<=512*1024
    (output/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']=='CORRECTNESS_STOP': raise SystemExit(2)


if __name__=='__main__': main()
