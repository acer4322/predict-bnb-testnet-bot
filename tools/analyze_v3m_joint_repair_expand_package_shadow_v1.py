from __future__ import annotations
import argparse,importlib.util,json,math,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v3m_local',HERE/'run_eth_v3m_asymmetric_geometry_enhanced_repair_smoke.py')
v3m=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3m)
v3b=v3m.v3b;EPS=v3m.EPS

def opp(s): return 'DOWN' if str(s)=='UP' else 'UP'

def geom_from(u,d,c):
    b=max(u,d)-c; w=min(u,d)-c
    r=0.0 if b>EPS and w>=-EPS else (abs(w)/b if b>EPS and w<0 else None)
    return b,w,r

class Shadow(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self,tape):
        super().__init__(tape);self.pkg_events=[]
    def _first_pair_legal(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for p in self._live_price_levels(side):
            p=round(float(p),10)
            if p in used: continue
            if not self._pair_ok(side,p): continue
            q=1.0/p
            if q<=EPS or q>12.0+EPS: continue
            return float(p),float(q)
        return None
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        before_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0)); before_ev=len(self.asym_events)
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        after_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        if after_fb>before_fb and len(self.asym_events)>before_ev:
            e=self.asym_events[-1]
            if e.get('event')=='ASYM_PASSIVE_GEOMETRY_CHECK' and not e.get('eligible'):
                repair_side=str(e['side']); expand_side=opp(repair_side); ex=self._first_pair_legal(expand_side)
                u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost)
                bb,bw,br=geom_from(u,d,c)
                pr=float(e['price']);qr=float(e['qty'])
                ur=u+(qr if repair_side=='UP' else 0.0);dr=d+(qr if repair_side=='DOWN' else 0.0);cr=c+qr*pr
                rb,rw,rr=geom_from(ur,dr,cr)
                rec={'event':'JOINT_PACKAGE_SHADOW','t':int(e['t']),'repairSide':repair_side,'repairRole':e.get('role'),'repairPrice':pr,'repairQty':qr,'beforeBest':bb,'beforeWorst':bw,'beforeRatio':br,'repairOnlyBest':rb,'repairOnlyWorst':rw,'repairOnlyRatio':rr,'occupancyBeforeSubmit':len(self.slot_key),'freeSlotsBeforeSubmit':max(0,self.max_slots-len(self.slot_key)),'expandCandidate':None,'packageEligible':False}
                if ex is not None:
                    pe,qe=ex
                    up=ur+(qe if expand_side=='UP' else 0.0);dp=dr+(qe if expand_side=='DOWN' else 0.0);cp=cr+qe*pe
                    pb,pw,rrp=geom_from(up,dp,cp)
                    eligible=(bb<=EPS) or (bw<0 and pb>EPS and rrp is not None and br is not None and rrp<=br+1e-12) or (bw>=-EPS and pw>=-EPS)
                    rec.update({'expandCandidate':{'side':expand_side,'price':pe,'qty':qe,'pairLegal':True},'packageBest':pb,'packageWorst':pw,'packageRatio':rrp,'packageEligible':bool(eligible),'packagePairPriceSum':pr+pe,'requiresTwoFreeSameClock':True,'hasTwoFreeSameClock':(self.max_slots-len(self.slot_key)>=2)})
                self.pkg_events.append(rec)
        return cand
    def run_shadow(self,w='__UNSCORED__'):
        r=super().run_v3m(w);r['jointPackageShadowEvents']=self.pkg_events;return r

def eqnum(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)): return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3m_pkg_') as td:
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
            ev=rb['jointPackageShadowEvents'];rows.append({'marketId':mid,'winnerPostHocOnly':winner,'behaviorParity':exact,'events':ev,'summary':{'events':len(ev),'expandAvailable':sum(e.get('expandCandidate') is not None for e in ev),'packageEligible':sum(bool(e.get('packageEligible')) for e in ev),'twoFree':sum(bool(e.get('hasTwoFreeSameClock')) for e in ev),'ledgerViolations':rb['quantityLedgerSummary'].get('invariantViolations')}})
            print(json.dumps({'progress':i,'marketId':mid,'parity':exact,**rows[-1]['summary']},ensure_ascii=False),flush=True)
    out={'version':'V3M_JOINT_REPAIR_EXPAND_PACKAGE_SHADOW_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'allBehaviorParity':all(r['behaviorParity'] for r in rows),'rows':rows,'boundary':['behavior-inert','evaluates rejected enhanced Repair alone vs rejected enhanced Repair plus one current pair-legal opposite/dominant-side minimum-notional Expand candidate','no new action/no winner runtime input','max4 unchanged','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
