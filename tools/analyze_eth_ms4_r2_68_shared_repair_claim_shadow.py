from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,deque
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_65_marketwide_loss_bearing_risk_wheel as r265
EPS=r265.EPS; REPAIR_ROLES=r265.REPAIR_ROLES

class SharedRepairClaimShadowSim(r265.MarketWideLossBearingRiskWheelSim):
    """Behavior-inert audit of submit-time fixed Repair quota vs fill-time shared responsibility.

    Before each actual physical fill reaches the native FIFO accounting, observe the current
    authoritative unmatched debt on the opposite side.  For a Repair-role fill, compute the
    fill-time shared claim:
        repair=min(fillInc,current_authoritative_debt)
        overflow=fillInc-repair
    Then compare that against the frozen V8 ROLE_FILL_SPLIT produced by existing per-key quota.

    No order, quota, credit, wheel state or native accounting is changed.
    """
    def __init__(self,*a,**kw):
        self.r268=Counter(); self.r268Events=[]; self._preFillClaims=deque(); self._r268SplitStart=0
        super().__init__(*a,**kw)

    def record_fill(self,t,side,q,p):
        # Owner queue was prepared by R2.65 in physical iteration order.
        owner=self._physicalFillOwners[0][0] if self._physicalFillOwners else None
        role=self.key_role.get(owner,'UNASSIGNED') if owner else 'UNASSIGNED'
        gen=self.key_scope_gen.get(owner) if owner else None
        scope_before=self.scopeSide; debt_before=float(self._scope_debt_qty()) if self.scopeSide is not None else 0.0
        repair_side=self._repair_side() if self.scopeSide is not None else None
        is_repair=bool(role in REPAIR_ROLES and repair_side is not None and str(side)==str(repair_side))
        shared_repair=min(float(q),debt_before) if is_repair else 0.0
        shared_overflow=max(0.0,float(q)-shared_repair) if role in REPAIR_ROLES else (float(q) if role=='SATELLITE_EXPAND' else 0.0)
        self._preFillClaims.append({'t':int(t),'key':owner,'role':role,'generationAtSubmit':gen,
            'scopeBefore':scope_before,'side':str(side),'price':float(p),'fillInc':float(q),
            'authoritativeDebtBeforeFill':debt_before,'sharedRepairAtFill':shared_repair,
            'sharedOverflowAtFill':shared_overflow,'repairSideBeforeFill':repair_side})
        return super().record_fill(t,side,q,p)

    def process(self,t):
        split_start=len(self.splitEvents); pre_count=len(self._preFillClaims)
        super().process(t)
        native_splits=[e for e in self.splitEvents[split_start:] if e.get('event')=='ROLE_FILL_SPLIT']
        new_claims=list(self._preFillClaims)[pre_count:]
        # In normal R2.65 all actual order fills should have both an attribution claim and ROLE_FILL_SPLIT.
        bykey=Counter()
        for e in native_splits:bykey[str(e.get('key'))]+=1
        used=Counter()
        for c in new_claims:
            key=str(c.get('key')); matches=[e for e in native_splits if str(e.get('key'))==key]
            idx=used[key]; n=matches[idx] if idx<len(matches) else None; used[key]+=1
            if n is None:
                self.r268['MISSING_NATIVE_SPLIT']+=1; continue
            nr=float(n.get('repairAllocated') or 0.0); no=float(n.get('overflowRealized') or 0.0)
            dr=float(c['sharedRepairAtFill'])-nr; do=float(c['sharedOverflowAtFill'])-no
            row={**c,'nativeRepairAllocated':nr,'nativeOverflowRealized':no,
                 'repairDeltaSharedMinusNative':dr,'overflowDeltaSharedMinusNative':do,
                 'nativeRepairQuotaRemainingAfter':float(self.keyRepairQuotaRemaining.get(key,0.0)),
                 'nativeOverflowQuotaRemainingAfter':float(self.keyOverflowQtyRemaining.get(key,0.0))}
            if c['role'] in REPAIR_ROLES:
                self.r268['REPAIR_FILL_EVENTS']+=1
                if abs(dr)>EPS or abs(do)>EPS:
                    self.r268['SHARED_NATIVE_SPLIT_DIVERGENCE']+=1
                    self.r268['DIVERGENCE_QTY_MICRO']+=int(round(abs(dr)*1_000_000))
                    if dr< -EPS:self.r268['NATIVE_OVERCLAIMED_REPAIR_EVENTS']+=1
                    if dr> EPS:self.r268['NATIVE_UNDERCLAIMED_REPAIR_EVENTS']+=1
            self.r268Events.append(row)

    def run_shadow(self,winner):
        r=super().run_r265(winner)
        div_qty=float(self.r268.get('DIVERGENCE_QTY_MICRO',0))/1_000_000.0
        r.update({'r268Version':'MS4_R2_68_SHARED_REPAIR_CLAIM_SHADOW_V1','r268Stats':dict(self.r268),
            'r268Events':self.r268Events[:6000],'r268DivergenceQty':div_qty,
            'r268BehaviorChanged':False})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r268_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];summary=[]
        for m in mids:
            s=SharedRepairClaimShadowSim(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(co[m]['winner'])
            finally:s.close()
            st=r['r268Stats'];row={'marketId':m,'pnl':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),
                'fills':int(r['fillEvents']),'repairFillEvents':int(st.get('REPAIR_FILL_EVENTS',0)),
                'divergenceEvents':int(st.get('SHARED_NATIVE_SPLIT_DIVERGENCE',0)),
                'nativeOverclaimEvents':int(st.get('NATIVE_OVERCLAIMED_REPAIR_EVENTS',0)),
                'nativeUnderclaimEvents':int(st.get('NATIVE_UNDERCLAIMED_REPAIR_EVENTS',0)),
                'divergenceQty':float(r['r268DivergenceQty']),'nativeQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
                'correctR265':bool(r['r265CorrectnessPass'])}
            summary.append(row);rows.append({'marketId':m,'winnerPostHocOnly':co[m]['winner'],**r});print(json.dumps(row,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_68_SHARED_REPAIR_CLAIM_SHADOW_RESULT_V1','researchOnly':True,'behaviorChanged':False,
            'markets':mids,'summary':summary,'rows':rows,
            'boundary':['exact R2.65 behavior','fill-time shared claim is diagnostic only','authoritative native FIFO debt observed before each physical fill','no quota/order/credit mutation','no Target/winner runtime input','consumed only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
