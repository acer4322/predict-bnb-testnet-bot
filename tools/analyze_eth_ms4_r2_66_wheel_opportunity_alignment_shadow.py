from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_65_marketwide_loss_bearing_risk_wheel as r265
EPS=r265.EPS

class WheelOpportunityAlignmentShadowSim(r265.MarketWideLossBearingRiskWheelSim):
    """Behavior-inert audit: characterize each R2.65 wheel submit against frozen OUR opportunity intent.

    No admission changes.  A wheel submit is tagged as opportunity-aligned when the existing
    strict-past direction signal equals current scopeSide.  It is tagged as an ordinary-credit-
    blocked Expand when that aligned candidate exists but its exact candidate-alone risk cost
    exceeds current frozen R2.47 monetary continuation credit.
    """
    def __init__(self,*a,**kw):
        self.r266=Counter();self.r266Events=[];self._r266Qv=None
        super().__init__(*a,**kw)

    def _open_one_option(self,t,qv,end):
        self._r266Qv=qv
        try:return super()._open_one_option(t,qv,end)
        finally:self._r266Qv=None

    def _try_pre_repair_risk_tranche(self,t,end):
        qv=self._r266Qv
        signal=None;state=None;cand=None;risk_cost=None;ordinary_credit=None
        aligned=False;credit_blocked=False
        if qv is not None:
            try:signal=str(self._direction(qv));state=str(self._state())
            except Exception:signal=None;state=None
        if self.scopeSide is not None:
            ordinary_credit=float(self._available_expand_risk_credit())
            try:cand=self._candidate_from_levels_v8(str(self.scopeSide),'SATELLITE_EXPAND',False)
            except Exception:cand=None
            if cand is not None:
                p,q,proj,split=cand
                risk_cost=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(str(self.scopeSide),p,q)))
            aligned=(state=='SCOPED' and signal==str(self.scopeSide))
            credit_blocked=bool(aligned and risk_cost is not None and ordinary_credit+EPS<risk_cost)
        before=int(self.r265.get('WHEEL_RISK_SUBMIT',0))
        ok=super()._try_pre_repair_risk_tranche(t,end)
        after=int(self.r265.get('WHEEL_RISK_SUBMIT',0))
        if ok and after>before:
            self.r266['WHEEL_SUBMIT']+=1
            self.r266['ALIGNED_WITH_OUR_SIGNAL']+=int(aligned)
            self.r266['NOT_ALIGNED_WITH_OUR_SIGNAL']+=int(not aligned)
            self.r266['ALIGNED_ORDINARY_CREDIT_BLOCKED']+=int(credit_blocked)
            self.r266['ALIGNED_NOT_CREDIT_BLOCKED']+=int(aligned and not credit_blocked)
            ev={'t':int(t),'event':'R266_WHEEL_SUBMIT_OPPORTUNITY_SHADOW','scopeSide':self.scopeSide,
                'signal':signal,'state':state,'aligned':bool(aligned),'ordinaryCreditBlocked':bool(credit_blocked),
                'ordinaryAvailableCredit':ordinary_credit,'candidateRiskCost':risk_cost,
                'wheelFreeBeforeApprox':float(self._wheel_free())}
            if cand is not None:ev.update({'candidatePrice':float(cand[0]),'candidateQty':float(cand[1])})
            self.r266Events.append(ev);self.slot_history.append(ev)
        return ok

    def run_r266_shadow(self,winner):
        r=super().run_r265(winner)
        r.update({'r266ShadowStats':dict(self.r266),'r266ShadowEvents':self.r266Events[:5000]})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r266_shadow_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];summary=[]
        for mid in mids:
            sim=WheelOpportunityAlignmentShadowSim(tmp/f'{mid}.json.xz',1,4)
            try:r=sim.run_r266_shadow(co[mid]['winner'])
            finally:sim.close()
            st=r['r266ShadowStats'];sub=int(st.get('WHEEL_SUBMIT',0));aligned=int(st.get('ALIGNED_WITH_OUR_SIGNAL',0));blocked=int(st.get('ALIGNED_ORDINARY_CREDIT_BLOCKED',0))
            row={'marketId':mid,'pnl':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),'best':float(r['best']),
                 'fills':int(r['fillEvents']),'wheelSubmits':int(r['wheelRiskSubmits']),
                 'shadowSubmits':sub,'aligned':aligned,'notAligned':int(st.get('NOT_ALIGNED_WITH_OUR_SIGNAL',0)),
                 'alignedCreditBlocked':blocked,'alignedNotCreditBlocked':int(st.get('ALIGNED_NOT_CREDIT_BLOCKED',0)),
                 'alignedShare':aligned/sub if sub else None,'creditBlockedShare':blocked/sub if sub else None,
                 'correct':bool(r['r265CorrectnessPass'])}
            summary.append(row);rows.append({'marketId':mid,'winnerPostHocOnly':co[mid]['winner'],**r});print(json.dumps(row,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_66_WHEEL_OPPORTUNITY_ALIGNMENT_SHADOW_V1','researchOnly':True,'behaviorChanged':False,
             'markets':mids,'summary':summary,'rows':rows,
             'boundary':['exact R2.65 behavior','strict-past OUR signal/scope only','no winner/Target runtime input','no admission mutation','consumed diagnostic only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
