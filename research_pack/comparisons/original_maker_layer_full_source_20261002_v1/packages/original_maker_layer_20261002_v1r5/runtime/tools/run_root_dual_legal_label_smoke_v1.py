"""Single dual-legal economic bundle choice, followed by frozen native continuation."""
from __future__ import annotations
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
from tools import run_root_btc5m_source_smoke_v1 as util

INPUTS = {'bt', 'events', 'times', 'raw', 'payload', 'meta', 'execModel'}


def policy_state(sim):
    return {k: v for k, v in vars(sim).items() if k not in INPUTS and not k.startswith('_lab')}


def signature(sim, result=None):
    return util.fingerprint({'policy': policy_state(sim), 'result': result})


class ReadOnlyBackend:
    def __init__(self, backend):
        self.backend = backend

    def __getattr__(self, name):
        if name not in {'orders', 'state_values', 'position', 'depth', 'current_timestamp'}:
            raise RuntimeError('preview forbids backend operation: '+name)
        return getattr(self.backend, name)


def preview_copy(sim):
    other = object.__new__(type(sim))
    other.__dict__.update(copy.deepcopy(policy_state(sim)))
    for k in INPUTS:
        if hasattr(sim, k):
            other.__dict__[k] = getattr(sim, k)
    other.bt = ReadOnlyBackend(sim.bt)
    return other


def complete_menu(sim, t):
    if sim.scopeSide is None or sim._has_stale_scope_reservation():
        return None
    if sim._last_new_receipt == t or len(sim.slot_key) >= sim.max_slots:
        return None
    repair = sim._repair_side()
    if sim._core_for_side(repair) is None:
        return None
    out = {}
    for label, side, role in [('R', repair, 'SATELLITE_REPAIR'),
                               ('E', sim.scopeSide, 'SATELLITE_EXPAND')]:
        c = preview_copy(sim)
        if len(c._live_role_rows(side=side)) >= c.max_slots:
            return None
        candidate = c._candidate_from_levels_v8(side, role, False)
        if candidate is None:
            return None
        p, q, projected, split = candidate
        if not all(math.isfinite(float(v)) for v in [p,q]) or not 0 < p < 1 or not 0 < q <= 12+1e-9:
            return None
        credit = c._available_expand_risk_credit()
        risk = max(0., c._physical_floor()-c._candidate_alone_floor(side,p,q))
        if label == 'E' and credit+1e-9 < risk:
            return None
        out[label] = {'side': side, 'role': role, 'price': p, 'qty': q,
                      'projection': projected, 'split': split, 'riskCost': risk,
                      'spendableCreditBefore': credit, 'generation': c.scopeGeneration,
                      'decision': [side,role,False,label=='R'],
                      'continuation': 'one native submit then unchanged R247 full suffix'}
    return out


def main():
    assert Path.cwd().resolve() == ROOT
    assert not (ROOT/'.lan_worker_v1/staging').exists()
    manifest = json.loads((ROOT/'DUAL_MANIFEST.json').read_text())
    for rel, sha in manifest['files'].items():
        assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() == sha, rel
    sys.path.insert(0, 'C:/BTC5M-worker/.tmp/hftbacktest_244')
    r247 = importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    import hftbacktest
    assert Path(hftbacktest.__file__).resolve() == Path('C:/BTC5M-worker/.tmp/hftbacktest_244/hftbacktest/__init__.py').resolve()
    base = r247.BoundedCoreServiceFavorableRecycleSim
    public = json.loads((ROOT/'dual_public.json').read_text())
    output = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    rows, comparisons = [], []
    attempted, started = 0, time.time()

    class Lab(base):
        def __init__(self, tape, branch, source):
            self._lab = {'branch':branch,'witness':None,'pending':None,'actual':None,
                         't':None,'observer':util.Observer(source),'proposalAudits':0}
            super().__init__(tape,1,4)

        def process(self,t):
            r=super().process(t)
            self._lab['observer'].post_process(self,t)
            return r

        def _open_one_option(self,t,qv,end):
            self._lab['t']=int(t)
            return super()._open_one_option(t,qv,end)

        def _role_decision(self,qv):
            native = super()._role_decision(qv)
            lab=self._lab
            if lab['branch']=='N' or lab['witness'] is not None or native is None:
                return native
            if native[1] not in {'SATELLITE_REPAIR','SATELLITE_EXPAND'}:
                return native
            before=signature(self)
            menu=complete_menu(self,lab['t'])
            assert signature(self)==before, 'preview mutated source policy'
            lab['proposalAudits']+=1
            if menu is None:
                return native
            ob=lab['observer']
            pub=util.latest_public(ob.public,ob.times,lab['t'])
            assert pub is None or pub['availableMs']<lab['t']
            lab['witness']={'t':lab['t'],'prefixSignature':before,'menu':util.clean(menu),
                            'nativeDecision':list(native),'public':pub,
                            'state':util.clean(util.state(self)),
                            'nativeCash':util.native_state(self),
                            'lifecyclePrefixSha256':util.fingerprint(policy_state(self))}
            if lab['branch']=='S':
                return native
            chosen=menu[lab['branch']]
            lab['pending']=chosen
            return tuple(chosen['decision'])

        def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
            pending=self._lab['pending']
            key=f'{side}_{self.n}'
            if pending is not None:
                expected=(pending['side'],pending['role'],pending['price'],pending['qty'],pending['projection'],pending['split'])
                assert util.fingerprint((side,role,p,q,proj,split))==util.fingerprint(expected), 'actual proposal differs'
            value=super()._submit_role_v8(t,side,role,p,q,proj,split)
            if pending is not None:
                assert value, 'pre-certified native proposal rejected'
                self._lab['actual']={'key':key,'t':t,'side':side,'role':role,'price':p,'qty':q,
                                      'split':split,'nativeAccepted':True}
                self._lab['pending']=None
            return value

    def run(mid, branch):
        nonlocal attempted
        attempted+=1
        assert attempted<=12
        (output/'BE_ACCOUNTING.json').write_text(json.dumps({'attemptedBE':attempted,'marketId':mid,'branch':branch,'oldITTUsed':0}),encoding='utf-8')
        print(json.dumps({'starting':mid,'branch':branch,'attemptedBE':attempted}),flush=True)
        sim=Lab(ROOT/f'tapes/{mid}.json.xz',branch,public[str(mid)])
        try:
            assert sim.inv=={'UP':0.,'DOWN':0.} and sim.cost==0.
            assert all(abs(v)<=1e-8 for v in util.native_state(sim).values())
            res=sim.run_r247('UP')
            assert sim.traj==[] and sim.target=={'UP':0.,'DOWN':0.}
            ob=sim._lab['observer']
            cash=util.cash_check(sim.inv,sim.cost,util.native_state(sim))
            correct=(cash['pass'] and all(v<=1e-8 for v in ob.max_error.values()) and
                     res['r247ServiceCorrectnessPass'] and res['unauthorizedOverflowQty']<=1e-9 and
                     res['repairQuotaExcessMax']<=1e-9 and sim.max_simultaneous_slots<=4)
            row={'marketId':mid,'branch':branch,'signature':signature(sim,res),'correct':correct,
                 'cashCheck':cash,'cashMaxError':ob.max_error,'native':util.native_state(sim),
                 'UP':sim.inv['UP']-sim.cost,'DOWN':sim.inv['DOWN']-sim.cost,
                 'floor':res['floor'],'best':res['best'],'cost':sim.cost,'fills':sim.fills,
                 'qty':sum(sim.inv.values()),'inventory':dict(sim.inv),'submits':sim.submits,
                 'scopeCompletions':sim.scopeCompletions,'scopeFlips':sim.scopeFlips,
                 'peakAbsNet':ob.peak_abs_net,'netIntegralShareMs':ob.abs_net_integral_ms,
                 'witness':sim._lab['witness'],'actual':sim._lab['actual'],
                 'previewAudits':sim._lab['proposalAudits'],'fillHistory':util.clean(sim.fillHist),
                 'orders':util.clean(sim.orders),'activeMeta':util.clean(sim.activeMeta),
                 'terminalAuthority':util.clean(util.state(sim)),'fullNetPnl':None,'realizedWinner':None}
            rows.append(row)
            assert correct, 'native cash / substrate correctness failure'
            print(json.dumps({'completed':mid,'branch':branch,'fills':sim.fills,'dualWitness':bool(row['witness'])}),flush=True)
            return row
        finally:
            sim.close()

    verdict='UNFINISHED'
    try:
        for mid in [2022527,2022538,2022602]:
            n=run(mid,'N'); s=run(mid,'S')
            assert n['signature']==s['signature'], 'N/S policy parity failure'
            if s['witness'] is None:
                comparisons.append({'marketId':mid,'exercised':False,'reason':'NO_DUAL_LEGAL_STATE'})
                continue
            r=run(mid,'R'); e=run(mid,'E')
            for x in [r,e]:
                assert x['actual'] is not None
                assert util.fingerprint(x['witness'])==util.fingerprint(s['witness']), 'prefix/menu mismatch'
            anti=all((n[k]<=0 or x[k]>=0.5*n[k]) for x in [r,e] for k in ['fills','qty','scopeCompletions'])
            delta={k:e[k]-r[k] for k in ['UP','DOWN','cost','fills','qty','scopeCompletions','scopeFlips','peakAbsNet','netIntegralShareMs']}
            response=any(abs(delta[k])>1e-8 for k in ['UP','DOWN'])
            comparisons.append({'marketId':mid,'exercised':True,'antiCollapse':anti,'nonzeroEndpointResponse':response,'E_minus_R':delta})
        exercised=[c for c in comparisons if c['exercised']]
        informative=[c for c in exercised if c['nonzeroEndpointResponse']]
        verdict=('NOT_EXERCISED_IN_SMOKE' if not exercised else
                 'ACTIVITY_COLLAPSE_CONFOUNDED' if not all(c['antiCollapse'] for c in exercised) else
                 'ZERO_VALUE_RESPONSE_IN_SMOKE' if not informative else 'LOCAL_CONTINUATION_LABEL_SUPPORT')
        promotion=len(informative)>=2 and all(c['antiCollapse'] for c in exercised)
    except Exception as exc:
        verdict='CORRECTNESS_STOP'; promotion=False
        print(json.dumps({'error':type(exc).__name__+': '+str(exc)}),flush=True)
        (output/'ERROR.json').write_text(json.dumps({'error':type(exc).__name__+': '+str(exc),'attemptedBE':attempted}),encoding='utf-8')
    result={'version':'ROOT_DUAL_LEGAL_CONTINUATION_LABEL_SMOKE3_V1','verdict':verdict,
            'attemptedBE':attempted,'elapsedSeconds':time.time()-started,'rows':rows,'comparison':comparisons,
            'labelSupportPromotion':promotion,'modelsTrained':0,'freshUsed':0,'oldITTUsed':0,
            'fullNetCost':'UNRESOLVED','trainingGate':'REQUIRES_SOURCE_COST_CLOSURE_AND_MARKET_SEPARATED_DESIGN',
            'targetInputUsed':False,'packageManifestSha256':hashlib.sha256((ROOT/'DUAL_MANIFEST.json').read_bytes()).hexdigest()}
    blob=json.dumps(util.clean(result),indent=2).encode()
    assert len(blob)<=1024**2,'output cap'
    (output/'COMPACT.json').write_bytes(blob)
    print(json.dumps({'verdict':verdict,'attemptedBE':attempted,'promotion':promotion,'comparison':comparisons}),flush=True)
    if verdict=='CORRECTNESS_STOP':
        raise SystemExit(2)


if __name__=='__main__':
    main()
