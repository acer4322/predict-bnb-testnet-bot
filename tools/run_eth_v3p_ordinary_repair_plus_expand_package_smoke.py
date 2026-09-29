from __future__ import annotations
import argparse,collections,importlib.util,json,os,tempfile,zipfile,sys
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

class V3P(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self,tape):
        super().__init__(tape);self.v3p_counter=collections.Counter();self.v3p_events=[];self._pkg_pending=None
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
        before_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        after_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0));a=self.q_arm
        if after_fb<=before_fb or cand is None or a is None:return cand
        self.v3p_counter['fallbackClocksSeen']+=1
        if self.max_slots-len(self.slot_key)<2:
            self.v3p_counter['packageNoTwoFreeSlots']+=1;return cand
        repair_side=str(side);repair_role=str(a['role']);pr=float(cand[0]);qr=float(cand[1]);expand_side=opp(repair_side);ex=self._first_pair_legal(expand_side)
        if ex is None:
            self.v3p_counter['packageNoExpandCandidate']+=1;return cand
        pe,qe=ex
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);bb,bw,br=geom(u,d,c)
        ur=u+(qr if repair_side=='UP' else 0.0);dr=d+(qr if repair_side=='DOWN' else 0.0);cr=c+qr*pr
        up=ur+(qe if expand_side=='UP' else 0.0);dp=dr+(qe if expand_side=='DOWN' else 0.0);cp=cr+qe*pe
        pb,pw,rr=geom(up,dp,cp)
        eligible=False
        if bb<=EPS:eligible=True
        elif bw>=-EPS:eligible=(pw>=-EPS)
        elif pb>EPS and br is not None and rr is not None and rr<=br+1e-12:eligible=True
        if not eligible:
            self.v3p_counter['packageGeometryRejected']+=1;return cand
        self._pkg_pending={'t':int(a['t']),'repairSide':repair_side,'repairRole':repair_role,'repairPrice':pr,'repairQty':qr,'expandSide':expand_side,'expandPrice':pe,'expandQty':qe,'beforeRatio':br,'packageRatio':rr,'responsibilityId':int(a['responsibilityId'])}
        self.v3p_counter['packageAuthorized']+=1
        return cand
    def _submit_role(self,t,side,role,p,q,proj,source):
        pending=self._pkg_pending;self._pkg_pending=None
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if not ok:return ok
        if pending is None:return ok
        if str(side)!=pending['repairSide'] or str(role)!=pending['repairRole'] or abs(float(p)-pending['repairPrice'])>EPS:return ok
        self.v3p_counter['packageRepairSubmits']+=1
        ok2=v3b.base.MinimalPairRoleSim._submit_role(self,int(t),pending['expandSide'],'SATELLITE_EXPAND',pending['expandPrice'],pending['expandQty'],None,'V3P_ORDINARY_REPAIR_PLUS_EXPAND')
        if ok2:
            self.v3p_counter['packageExpandSubmits']+=1
            self.v3p_events.append({'event':'V3P_PACKAGE_MATERIALIZED','t':int(t),'repairSide':pending['repairSide'],'repairPrice':pending['repairPrice'],'repairQty':pending['repairQty'],'expandSide':pending['expandSide'],'expandPrice':pending['expandPrice'],'expandQty':pending['expandQty'],'responsibilityId':pending['responsibilityId'],'beforeRatio':pending['beforeRatio'],'packageRatio':pending['packageRatio']})
        else:self.v3p_counter['packageSecondSubmitFailures']+=1
        return ok
    def run_v3p(self,w='__UNSCORED__'):
        r=super().run_v3m(w);r['v3pCounters']=dict(self.v3p_counter);r['v3pEvents']=self.v3p_events[:5000];r['v3pOrdinaryRepairPlusExpandPackage']=True;return r

def metrics(rows,cell):return v3m.metrics(rows,cell)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3p_pkg_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls,fn in [('A_V3B',v3b.FifoAggregateResponsibilityLadderV3B,'run_qty'),('B_V3M',v3m.AsymmetricGeometryEnhancedRepairV3M,'run_v3m'),('C_V3P',V3P,'run_v3p')]:
                sim=Cls(tape)
                try:r=getattr(sim,fn)('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);rows.append({'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r})
            A,B,C=rows[-3:]
            print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':A['pnlDiagnosticOnly'],'fills':A['fillEvents'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'fills':B['fillEvents'],'alts':B['fillSideAlternations']},'C':{'pnl':C['pnlDiagnosticOnly'],'floor':C['floor'],'best':C['best'],'fills':C['fillEvents'],'alts':C['fillSideAlternations'],'maxSlots':C['maxSimultaneousSlots'],'v3p':C.get('v3pCounters'),'ledgerViolations':C['quantityLedgerSummary'].get('invariantViolations')}},ensure_ascii=False),flush=True)
    out={'version':'V3P_ORDINARY_REPAIR_PLUS_EXPAND_PACKAGE_SMOKE','date':'2026-09-06','researchOnly':True,'markets':mids,'rows':rows,'summary':{c:metrics(rows,c) for c in ['A_V3B','B_V3M','C_V3P']},'boundary':['V3M enhanced Repair rejection unchanged','ordinary Pair-Core Repair remains first action','same-receipt opposite/dominant-side Pair-legal minimum-notional Expand only with two real free max4 slots and joint branch-ratio non-worsening','no max5/cancel/slot replacement','no winner/PnL runtime authority','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
