from __future__ import annotations
import argparse, importlib.util, json, math, os, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('v3m_local', HERE/'run_eth_v3m_asymmetric_geometry_enhanced_repair_smoke.py')
v3m = importlib.util.module_from_spec(spec); spec.loader.exec_module(v3m)
v3b = v3m.v3b
EPS = v3m.EPS

class FirstFallbackShadow(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self, tape):
        super().__init__(tape)
        self.first_fallback = None

    def _capture_first(self, a, side, p, q, g):
        if self.first_fallback is not None or a is None:
            return
        qv = a.get('qv') or {}
        try:
            mid_up = 0.5*(float(qv['UP']['bid']) + float(qv['UP']['ask']))
            mid_dn = 0.5*(float(qv['DOWN']['bid']) + float(qv['DOWN']['ask']))
        except Exception:
            mid_up = mid_dn = None
        den = (mid_up + mid_dn) if mid_up is not None and mid_dn is not None else None
        prob_up = (mid_up/den) if den and den > EPS else None
        prob_dn = (mid_dn/den) if den and den > EPS else None
        up_qty = float(self.inv['UP']); dn_qty = float(self.inv['DOWN']); cost = float(self.cost)
        up_branch = up_qty - cost; dn_branch = dn_qty - cost
        dom = 'UP' if up_qty >= dn_qty else 'DOWN'
        p_dom = prob_up if dom=='UP' else prob_dn
        p_weak = prob_dn if dom=='UP' else prob_up
        weighted_ev = None
        if prob_up is not None and prob_dn is not None:
            weighted_ev = prob_up*up_branch + prob_dn*dn_branch
        self.first_fallback = {
            't': int(a['t']), 'repairSide': str(side), 'repairRole': str(a['role']),
            'responsibilityId': int(a['responsibilityId']), 'candidatePrice': float(p), 'candidateQty': float(q),
            'upQty': up_qty, 'downQty': dn_qty, 'cost': cost,
            'upBranch': up_branch, 'downBranch': dn_branch,
            'dominantInventorySide': dom, 'dominantProbabilityMid': p_dom, 'weakProbabilityMid': p_weak,
            'midUP': mid_up, 'midDOWN': mid_dn, 'normalizedProbUP': prob_up, 'normalizedProbDOWN': prob_dn,
            'midpointWeightedPortfolioEV': weighted_ev,
            'repairOpposesDominant': bool(side != dom),
            'beforeBest': g.get('beforeBest'), 'beforeWorst': g.get('beforeWorst'), 'beforeRatio': g.get('beforeRatio'),
            'afterBestEnhancedRepair': g.get('afterBest'), 'afterWorstEnhancedRepair': g.get('afterWorst'),
            'afterRatioEnhancedRepair': g.get('afterRatio'), 'geometryReason': g.get('reason')
        }

    def _candidate_from_levels(self, side, require_pair=True, require_budget=False):
        before_fb = int(self.asym_counter.get('passiveGeometryFallbacks', 0))
        cand = super()._candidate_from_levels(side, require_pair, require_budget)
        after_fb = int(self.asym_counter.get('passiveGeometryFallbacks', 0))
        if after_fb > before_fb and self.first_fallback is None:
            a = self.q_arm
            if a is not None:
                ev = self.asym_events[-1] if self.asym_events else {}
                p = float(ev.get('price') or a.get('passivePrice') or 0.0)
                q = float(ev.get('qty') or a.get('passiveQty') or 0.0)
                self._capture_first(a, side, p, q, ev)
        return cand

    def run_shadow(self, winner='__UNSCORED__'):
        r = super().run_v3m(winner)
        r['firstGeometryFallbackShadow'] = self.first_fallback
        return r

def metrics(rows, cell):
    return v3m.metrics(rows, cell)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--bundle', required=True); ap.add_argument('--market-ids', required=True); ap.add_argument('--output', required=True)
    a = ap.parse_args(); mids = [int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]
    with tempfile.TemporaryDirectory(prefix='v3m_midpoint_shadow_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids: z.extract(f'tapes/{mid}.json.xz', root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz'; winner=str(cohort[mid]['winner']).upper()
            A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try: ra=A.run_qty('__UNSCORED__')
            finally: A.close()
            B=FirstFallbackShadow(tape)
            try: rb=B.run_shadow('__UNSCORED__')
            finally: B.close()
            ra['pnlDiagnosticOnly']=float(ra['upQty' if winner=='UP' else 'downQty'])-float(ra['buyNotional'])
            rb['pnlDiagnosticOnly']=float(rb['upQty' if winner=='UP' else 'downQty'])-float(rb['buyNotional'])
            ar={'marketId':mid,'cell':'A_V3B','winnerPostHocOnly':winner,**ra}; br={'marketId':mid,'cell':'B_V3M_SHADOW','winnerPostHocOnly':winner,**rb}
            rows += [ar,br]
            f=rb.get('firstGeometryFallbackShadow')
            print(json.dumps({'progress':i,'marketId':mid,'deltaPnl':rb['pnlDiagnosticOnly']-ra['pnlDiagnosticOnly'],'deltaFills':rb['fillEvents']-ra['fillEvents'],'firstFallback':f,'ledgerViolations':rb['quantityLedgerSummary'].get('invariantViolations')},ensure_ascii=False),flush=True)
    out={'version':'V3M_FIRST_FALLBACK_MIDPOINT_THESIS_SHADOW_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V3B':metrics(rows,'A_V3B'),'B_V3M_SHADOW':metrics(rows,'B_V3M_SHADOW')},'boundary':['first passive geometry fallback only; pre-divergence strict-past state','binary midpoint used only as behavior-inert shadow','winner/PnL only posthoc scoring','no action change beyond frozen V3M','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False))
if __name__=='__main__': main()
