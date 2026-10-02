from __future__ import annotations
import argparse, json, os, shutil, tempfile, threading, time, zipfile
from pathlib import Path
import sys, importlib, importlib.util

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
if str(ROOT / 'tools') not in sys.path: sys.path.insert(0, str(ROOT / 'tools'))

print('IMPORT_STAGE joblib START', flush=True)
import joblib
print('IMPORT_STAGE joblib PASS', flush=True)

def _load_or_staged(fullname: str, filename: str):
    print(f'IMPORT_STAGE {fullname} START', flush=True)
    try:
        m = importlib.import_module(fullname)
        print(f'IMPORT_STAGE {fullname} PASS project', flush=True)
        return m
    except ImportError:
        p = Path(__file__).with_name(filename)
        spec = importlib.util.spec_from_file_location(fullname, p)
        if spec is None or spec.loader is None: raise ImportError(p)
        m = importlib.util.module_from_spec(spec); sys.modules[fullname] = m; spec.loader.exec_module(m)
        print(f'IMPORT_STAGE {fullname} PASS staged', flush=True)
        return m

_load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
_load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
_load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
_load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
_load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg = _load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
pe = pg.pe
EPS = 1e-9

class InitialRiskActiveProbeShadow(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.initialRiskProbeShadow = []

    def _start_reserve_builder(self,t,qv):
        before_none = self.thesis is None and self.reserveBuilder is None and self.repairParent is None and self.outstanding_total() <= EPS
        floor,u,d,cost = self._raw_floor()
        side = None
        shadow = None
        if before_none:
            side = self._signal_side(qv)
            bid = float(qv[side]['bid'])
            ask = float(qv[side].get('ask') or 0.0)
            opp = 'DOWN' if side == 'UP' else 'UP'
            opp_bid = float(qv[opp]['bid'])
            passive_q = 1.0 / bid if bid > EPS else 1e99
            active_q = 1.0 / ask if ask > EPS else 1e99
            if side == 'UP': hu, hd = float(u)+active_q, float(d)
            else: hu, hd = float(u), float(d)+active_q
            hcost = float(cost) + (ask * active_q if ask > EPS and active_q < 1e50 else 0.0)
            hfloor = min(hu,hd)-hcost if active_q < 1e50 else None
            hbest = max(hu,hd)-hcost if active_q < 1e50 else None
            start = int(self.capEnd)-300000
            phase = (int(t)-start)/300000.0
            shadow = {
                't': int(t), 'normalizedPhase': phase, 'side': side,
                'floorBefore': float(floor), 'upBefore': float(u), 'downBefore': float(d), 'costBefore': float(cost),
                'passiveBid': bid, 'passiveQty': passive_q,
                'activeAsk': ask, 'activeLegalQty': active_q,
                'activeGrossCost': (ask*active_q if ask > EPS and active_q < 1e50 else None),
                'hypActiveFloor': hfloor, 'hypActiveBest': hbest,
                'oppositeBid': opp_bid, 'bidPairSum': bid+opp_bid,
                'before180Fence': int(self.capEnd)-int(t) > 180000,
                'activeVenueLegal': bool(ask > EPS and active_q > EPS and active_q <= 12.0+EPS),
                'activeInsideOneDollarEnvelope': bool(ask > EPS and active_q < 1e50 and ask*active_q <= 1.0000001),
            }
        ok = super()._start_reserve_builder(t,qv)
        if shadow is not None:
            rb = self.reserveBuilder
            shadow['passiveSubmitOk'] = bool(ok)
            shadow['passiveKey'] = rb.get('firstKey') if ok and rb else None
            shadow['passiveSubmittedAt'] = int(t) if ok else None
            self.initialRiskProbeShadow.append(shadow)
        return ok

    def finalize_probe_shadow(self):
        fill_by_key = {}
        for e in getattr(self,'v53Fills',[]) or []:
            k=e.get('key')
            if k: fill_by_key[k]=fill_by_key.get(k,0.0)+float(e.get('qty') or 0.0)
        for z in self.initialRiskProbeShadow:
            k=z.get('passiveKey')
            z['passiveActualFillQty']=float(fill_by_key.get(k,0.0)) if k else 0.0
            z['passiveMaterialized']=z['passiveActualFillQty']>EPS
            z['shadowActiveSupport']=bool(z.get('passiveSubmitOk') and not z['passiveMaterialized'] and z.get('activeVenueLegal') and z.get('activeInsideOneDollarEnvelope') and z.get('before180Fence'))
        return self.initialRiskProbeShadow

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    outp=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output)
    tmp=Path(tempfile.mkdtemp(prefix='initial_risk_active_probe_shadow_')); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'INITIAL_RISK_ACTIVE_PROBE_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'INITIAL_RISK_ACTIVE_PROBE_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']; by={int(r['marketId']):r for r in cohort}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid]; sim=pe.make(InitialRiskActiveProbeShadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
            try:
                r=sim.run_guard(models,cr['winner']); probes=sim.finalize_probe_shadow(); ss=pe.safety(r); cons,bound,_=pe.alloc(sim,r)
            finally: sim.close()
            row={'marketId':mid,'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'probes':probes,'safety':ss,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound)}
            rows.append(row); print(json.dumps({'progress':f'{i}/{len(mids)}','marketId':mid,'fills':row['fills'],'probes':len(probes),'support':sum(bool(z.get('shadowActiveSupport')) for z in probes)},ensure_ascii=False),flush=True)
        support=sum(sum(bool(z.get('shadowActiveSupport')) for z in r['probes']) for r in rows)
        attempted=sum(len(r['probes']) for r in rows)
        out={'version':'INITIAL_RISK_ACTIVE_PROBE_SHADOW_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'behaviorChange':False,'marketIds':mids,'decision':'SHADOW_SUPPORT_INITIAL_RISK_ACTIVE_ROUTE' if support>0 else 'SHADOW_NO_SUPPORT_INITIAL_RISK_ACTIVE_ROUTE','summary':{'probeAttempts':attempted,'supportedUnfilledPassiveAttempts':support},'rows':rows,'boundary':['OUR side unchanged','behavior-inert','same-clock strict-past ask only','one minimum-legal ~1 USDT Active carrier shadow','no fixed wait seconds','no Target runtime input','no dream fill','no 8781']}
        outp.parent.mkdir(parents=True,exist_ok=True); outp.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'decision':out['decision'],'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
