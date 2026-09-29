from __future__ import annotations
import argparse,collections,importlib.util,json,math,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v3m_local',HERE/'run_eth_v3m_asymmetric_geometry_enhanced_repair_smoke.py')
v3m=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3m)
v3b=v3m.v3b;EPS=v3m.EPS

class Shadow(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self,tape):
        super().__init__(tape); self.events=[]; self._fb=None
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        b=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        a=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        arm=self.q_arm
        if a>b and cand is not None and arm is not None:
            self._fb={'t':int(arm['t']),'repairSide':str(side),'repairRole':str(arm['role']),'responsibilityId':int(arm['responsibilityId'])}
        return cand
    def _slot_rows(self):
        out=[]
        for sid,key in sorted(self.slot_key.items()):
            o=self.orders.get(key) or {}
            try:s=self.snap(o) if o else {}
            except Exception:s={}
            out.append({'slotId':int(sid),'key':key,'role':self.key_role.get(key,'UNASSIGNED'),'side':o.get('side'),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'cum':float(o.get('cum') or 0.0),'cancelRequested':bool(o.get('cancelRequested')),'status':str(s.get('status') or '').upper(),'cancellable':bool(s.get('cancellable')) if 'cancellable' in s else None})
        return out
    def _submit_role(self,t,side,role,p,q,proj,source):
        pending=self._fb; self._fb=None
        before=self._slot_rows() if pending is not None else None
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok and pending is not None and side==pending['repairSide'] and role==pending['repairRole']:
            after=self._slot_rows()
            counts=collections.Counter(r['role'] for r in after)
            self.events.append({'t':int(t),**pending,'submittedSide':str(side),'submittedRole':str(role),'submittedPrice':float(p),'slotsBefore':before,'slotsAfter':after,'occupancyAfter':len(after),'roleCountsAfter':dict(counts),'sameSideRepairLiveAfter':sum(1 for r in after if r['side']==side and r['role'] in {'ECONOMIC_CORE','SATELLITE_REPAIR'}),'expandLiveAfter':sum(1 for r in after if r['role']=='SATELLITE_EXPAND'),'cancelPendingAfter':sum(1 for r in after if r['cancelRequested'])})
        return ok
    def run_shadow(self,w='__UNSCORED__'):
        r=super().run_v3m(w);r['fallbackCapacityShadowEvents']=self.events;return r

def eqnum(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3m_fb_capacity_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            A=v3m.AsymmetricGeometryEnhancedRepairV3M(tape)
            try:ra=A.run_v3m('__UNSCORED__')
            finally:A.close()
            B=Shadow(tape)
            try:rb=B.run_shadow('__UNSCORED__')
            finally:B.close()
            exact=all(eqnum(ra.get(k),rb.get(k)) for k in ['submits','fillEvents','upQty','downQty','buyNotional','floor','best','fillSideAlternations'])
            rows.append({'marketId':mid,'winnerPostHocOnly':winner,'behaviorParity':exact,'events':rb['fallbackCapacityShadowEvents'],'summary':{'fills':rb['fillEvents'],'submits':rb['submits'],'alts':rb['fillSideAlternations'],'ledgerViolations':rb['quantityLedgerSummary'].get('invariantViolations')}})
            print(json.dumps({'progress':i,'marketId':mid,'parity':exact,'events':len(rb['fallbackCapacityShadowEvents']),'occ':collections.Counter(e['occupancyAfter'] for e in rb['fallbackCapacityShadowEvents']),'roles':[{k:int(v) for k,v in e['roleCountsAfter'].items()} for e in rb['fallbackCapacityShadowEvents']]},ensure_ascii=False),flush=True)
    out={'version':'V3M_GEOMETRY_FALLBACK_CAPACITY_SHADOW_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'allBehaviorParity':all(r['behaviorParity'] for r in rows),'rows':rows,'boundary':['behavior-inert shadow of V3M geometry fallback ordinary Repair submits','no winner/PnL action authority','records exact slot_key occupancy and role composition after fallback submit','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
