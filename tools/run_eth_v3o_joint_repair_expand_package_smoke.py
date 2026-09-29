from __future__ import annotations
import argparse,collections,importlib.util,json,math,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v3m_local',HERE/'run_eth_v3m_asymmetric_geometry_enhanced_repair_smoke.py')
v3m=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3m)
v3b=v3m.v3b;EPS=v3m.EPS

def opp(s): return 'DOWN' if str(s)=='UP' else 'UP'
def geom(u,d,c):
    b=max(u,d)-c;w=min(u,d)-c
    r=0.0 if b>EPS and w>=-EPS else (abs(w)/b if b>EPS and w<0 else None)
    return b,w,r

class V3O(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self,tape):
        super().__init__(tape);self.v3o_counter=collections.Counter();self.v3o_events=[];self._pkg_pending=None
    def _first_pair_legal(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for p in self._live_price_levels(side):
            p=round(float(p),10)
            if p in used:continue
            if not self._pair_ok(side,p):continue
            q=1.0/p
            if q<=EPS or q>12.0+EPS:continue
            return float(p),float(q)
        return None
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        before_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0));before_ev=len(self.asym_events)
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        after_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0));a=self.q_arm
        if after_fb<=before_fb or len(self.asym_events)<=before_ev or a is None:return cand
        e=self.asym_events[-1]
        if e.get('event')!='ASYM_PASSIVE_GEOMETRY_CHECK' or bool(e.get('eligible')):return cand
        self.v3o_counter['fallbackClocksSeen']+=1
        # Package needs two free slots before either physical submit. Never evict or raise max4.
        if self.max_slots-len(self.slot_key)<2:
            self.v3o_counter['packageNoTwoFreeSlots']+=1;return cand
        repair_side=str(e['side']);expand_side=opp(repair_side);ex=self._first_pair_legal(expand_side)
        if ex is None:
            self.v3o_counter['packageNoExpandCandidate']+=1;return cand
        pr=float(e['price']);qr=float(e['qty']);pe,qe=ex
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);bb,bw,br=geom(u,d,c)
        ur=u+(qr if repair_side=='UP' else 0.0);dr=d+(qr if repair_side=='DOWN' else 0.0);cr=c+qr*pr
        up=ur+(qe if expand_side=='UP' else 0.0);dp=dr+(qe if expand_side=='DOWN' else 0.0);cp=cr+qe*pe
        pb,pw,rr=geom(up,dp,cp)
        eligible=False
        if bb<=EPS:eligible=True
        elif bw>=-EPS:eligible=(pw>=-EPS)
        elif pb>EPS and br is not None and rr is not None and rr<=br+1e-12:eligible=True
        if not eligible:
            self.v3o_counter['packageGeometryRejected']+=1;return cand
        # Restore the original enhanced passive marker that V3M removed on fallback.
        a['passivePrice']=pr;a['passiveQty']=qr
        self._pkg_pending={'t':int(e['t']),'repairSide':repair_side,'repairRole':str(e['role']),'repairPrice':pr,'repairQty':qr,'expandSide':expand_side,'expandPrice':pe,'expandQty':qe,'beforeRatio':br,'packageRatio':rr,'beforeBest':bb,'beforeWorst':bw,'packageBest':pb,'packageWorst':pw,'responsibilityId':int(a['responsibilityId'])}
        self.v3o_counter['packageOverrides']+=1
        self.v3o_events.append({'event':'V3O_PACKAGE_AUTHORIZED',**self._pkg_pending})
        # cand currently points to ordinary fallback; replace only the physical repair route with original enhanced repair.
        proj=cand[2] if cand is not None else None
        return float(pr),float(qr),proj
    def _submit_role(self,t,side,role,p,q,proj,source):
        pending=self._pkg_pending
        # Preserve pending until repair submit is known to succeed.
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if not ok:return ok
        if pending is None:return ok
        if str(side)!=pending['repairSide'] or str(role)!=pending['repairRole'] or abs(float(p)-pending['repairPrice'])>EPS:return ok
        self._pkg_pending=None
        self.v3o_counter['packageRepairSubmits']+=1
        # Prechecked second slot and Pair-legal candidate; submit through minimal Pair-Core physical lane.
        ok2=v3b.base.MinimalPairRoleSim._submit_role(self,int(t),pending['expandSide'],'SATELLITE_EXPAND',pending['expandPrice'],pending['expandQty'],None,'V3O_JOINT_PACKAGE_EXPAND')
        if ok2:
            self.v3o_counter['packageExpandSubmits']+=1
            self.v3o_events.append({'event':'V3O_PACKAGE_MATERIALIZED','t':int(t),'repairSide':pending['repairSide'],'repairPrice':pending['repairPrice'],'repairQty':pending['repairQty'],'expandSide':pending['expandSide'],'expandPrice':pending['expandPrice'],'expandQty':pending['expandQty'],'responsibilityId':pending['responsibilityId'],'beforeRatio':pending['beforeRatio'],'packageRatio':pending['packageRatio']})
        else:
            self.v3o_counter['packageSecondSubmitFailures']+=1
            self.v3o_events.append({'event':'V3O_PACKAGE_SECOND_SUBMIT_FAILED','t':int(t),'responsibilityId':pending['responsibilityId']})
        return ok
    def run_v3o(self,w='__UNSCORED__'):
        r=super().run_v3m(w);r['v3oCounters']=dict(self.v3o_counter);r['v3oEvents']=self.v3o_events[:5000];r['v3oJointRepairExpandPackage']=True;return r

def metrics(rows,cell):return v3m.metrics(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3o_pkg_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls,fn in [('A_V3B',v3b.FifoAggregateResponsibilityLadderV3B,'run_qty'),('B_V3M',v3m.AsymmetricGeometryEnhancedRepairV3M,'run_v3m'),('C_V3O',V3O,'run_v3o')]:
                sim=Cls(tape)
                try:r=getattr(sim,fn)('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);rows.append({'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r})
            A,B,C=rows[-3:]
            print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':A['pnlDiagnosticOnly'],'fills':A['fillEvents'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'fills':B['fillEvents'],'alts':B['fillSideAlternations']},'C':{'pnl':C['pnlDiagnosticOnly'],'floor':C['floor'],'best':C['best'],'fills':C['fillEvents'],'alts':C['fillSideAlternations'],'maxSlots':C['maxSimultaneousSlots'],'v3o':C.get('v3oCounters'),'ledgerViolations':C['quantityLedgerSummary'].get('invariantViolations')}},ensure_ascii=False),flush=True)
    out={'version':'V3O_JOINT_REPAIR_EXPAND_PACKAGE_SMOKE','date':'2026-09-06','researchOnly':True,'markets':mids,'rows':rows,'summary':{c:metrics(rows,c) for c in ['A_V3B','B_V3M','C_V3O']},'boundary':['V3M exact-FIFO and asymmetric geometry retained','only geometry-fallback clocks with two actual free max4 slots can materialize joint package','enhanced Repair first plus one same-receipt opposite/dominant-side Pair-legal minimum-notional SATELLITE_EXPAND','joint package must be winner-free branch-ratio non-worsening; no hard 1/3 threshold','no cancellation/slot replacement/max5','no winner/PnL runtime authority','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
