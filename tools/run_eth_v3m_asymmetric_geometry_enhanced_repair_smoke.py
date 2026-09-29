from __future__ import annotations
import argparse,collections,json,math,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS;TICK=v3b.TICK

class AsymmetricGeometryEnhancedRepairV3M(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape)
        self.asym_counter=collections.Counter();self.asym_events=[]

    def _geom(self,side,p,q):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost)
        bb=max(u,d)-c;bw=min(u,d)-c
        br=(0.0 if bb>EPS and bw>=-EPS else (abs(bw)/bb if bb>EPS and bw<0 else None))
        u2=u+(float(q) if side=='UP' else 0.0);d2=d+(float(q) if side=='DOWN' else 0.0);c2=c+float(q)*float(p)
        ab=max(u2,d2)-c2;aw=min(u2,d2)-c2
        ar=(0.0 if ab>EPS and aw>=-EPS else (abs(aw)/ab if ab>EPS and aw<0 else None))
        if bb<=EPS:
            ok=True;reason='NO_POSITIVE_BRANCH_INHERIT_V3B'
        elif bw>=-EPS:
            ok=False;reason='BOTH_BRANCHES_NONNEGATIVE_NO_ENHANCED_REPAIR'
        elif ab<=EPS or ar is None:
            ok=False;reason='PROJECTED_POSITIVE_BRANCH_DESTROYED'
        elif ar<=br+1e-12:
            ok=True;reason='RATIO_NONWORSENING'
        else:
            ok=False;reason='RATIO_WORSENING'
        return ok,{'beforeBest':bb,'beforeWorst':bw,'beforeRatio':br,'afterBest':ab,'afterWorst':aw,'afterRatio':ar,'reason':reason}

    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        cand=super()._candidate_from_levels(side,require_pair,require_budget);a=self.q_arm
        if cand is None or a is None or 'passivePrice' not in a:return cand
        p,q,proj=cand
        if abs(float(p)-float(a.get('passivePrice') or -99))>EPS:return cand
        ok,g=self._geom(side,float(p),float(q));self.asym_counter['passiveGeometryChecks']+=1
        self.asym_events.append({'event':'ASYM_PASSIVE_GEOMETRY_CHECK','t':int(a['t']),'side':side,'role':a['role'],'price':float(p),'qty':float(q),'responsibilityId':a['responsibilityId'],'eligible':bool(ok),**g})
        if ok:
            self.asym_counter['passiveGeometryAllows']+=1;return cand
        self.asym_counter['passiveGeometryFallbacks']+=1
        # Remove enhanced marker so an ordinary Pair-Core submit cannot be mis-owned by the managed ladder.
        for k in ('passivePrice','passiveQty'):a.pop(k,None)
        ordinary=v3b.base.MinimalPairRoleSim._candidate_from_levels(self,side,require_pair,require_budget)
        if ordinary is not None:self.asym_counter['ordinaryRepairAvailableAfterPassiveFallback']+=1
        else:self.asym_counter['ordinaryRepairUnavailableAfterPassiveFallback']+=1
        return ordinary

    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        if pnd is None or L is None:return False
        target=self._aggregate_outstanding_expand_side(pnd['targetExpandSide'])
        if target<=EPS:return super()._submit_protected_active_qty(t,qv)
        side=pnd['side'];ask=float(qv[side]['ask']);limit=round(min(.99,ask+TICK),10);min_qty=1.0/limit
        qty=min(float(pnd['sourceRemainingQty']),target)
        if qty+EPS<min_qty:return super()._submit_protected_active_qty(t,qv)
        ok,g=self._geom(side,limit,qty);self.asym_counter['activeGeometryChecks']+=1
        self.asym_events.append({'event':'ASYM_ACTIVE_GEOMETRY_CHECK','t':int(t),'side':side,'role':pnd['role'],'limitPrice':limit,'qty':qty,'originResponsibilityId':pnd['originResponsibilityId'],'eligible':bool(ok),**g})
        if ok:
            self.asym_counter['activeGeometryAllows']+=1;return super()._submit_protected_active_qty(t,qv)
        self.asym_counter['activeGeometryHandoffs']+=1
        self._complete_carrier(t,'ASYM_GEOMETRY_ACTIVE_HANDOFF_TO_ORDINARY')
        return False

    def run_v3m(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['asymmetricGeometryV3M']=True;r['asymmetryCounters']=dict(self.asym_counter);r['asymmetryEvents']=self.asym_events[:5000];return r

def metrics(rows,cell):
    xs=[r for r in rows if r['cell']==cell];pn=[float(r['pnlDiagnosticOnly']) for r in xs];wins=[x for x in pn if x>EPS];loss=[-x for x in pn if x<-EPS]
    quality=0;rat=[]
    for r in xs:
        b=float(r['best']);w=float(r['floor'])
        rr=(0.0 if b>EPS and w>=-EPS else (abs(w)/b if b>EPS and w<0 else None))
        if rr is not None:rat.append(rr)
        if b>2 and rr is not None and rr<1/3:quality+=1
    return {'markets':len(xs),'winRate':len(wins)/len(xs),'avgPnl':sum(pn)/len(xs),'meanWin':sum(wins)/len(wins) if wins else None,'winsGt2Share':sum(x>2 for x in wins)/len(wins) if wins else None,'meanLossMagnitude':sum(loss)/len(loss) if loss else None,'lossLt1Share':sum(x<1 for x in loss)/len(loss) if loss else None,'worstPnl':min(pn),'avgFills':sum(int(r['fillEvents']) for r in xs)/len(xs),'avgAlternations':sum(int(r.get('fillSideAlternations') or 0) for r in xs)/len(xs),'tradeCoverage':sum(int(r['fillEvents'])>0 for r in xs)/len(xs),'twoSidedCoverage':sum(bool(r.get('twoSidedMaterialized')) for r in xs)/len(xs),'qualityBestGt2RiskLtThird':quality/len(xs),'medianPositiveBestRiskRatio':sorted(rat)[len(rat)//2] if rat else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='v3m_asym_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3M_ASYM_GEOMETRY',AsymmetricGeometryEnhancedRepairV3M)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__') if cell=='A_V3B' else sim.run_v3m('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);rows.append({'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r})
            aa=rows[-2];bb=rows[-1]
            print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':aa['pnlDiagnosticOnly'],'floor':aa['floor'],'best':aa['best'],'fills':aa['fillEvents'],'alts':aa['fillSideAlternations']},'B':{'pnl':bb['pnlDiagnosticOnly'],'floor':bb['floor'],'best':bb['best'],'fills':bb['fillEvents'],'alts':bb['fillSideAlternations'],'asym':bb.get('asymmetryCounters'),'ledgerViolations':bb['quantityLedgerSummary'].get('invariantViolations')}},ensure_ascii=False),flush=True)
    out={'version':'V3M_ASYMMETRIC_GEOMETRY_ENHANCED_REPAIR_SMOKE','date':'2026-09-06','researchOnly':True,'markets':mids,'rows':rows,'summary':{'A_V3B':metrics(rows,'A_V3B'),'B_V3M_ASYM_GEOMETRY':metrics(rows,'B_V3M_ASYM_GEOMETRY')},'boundary':['V3B exact-FIFO responsibility unchanged','ordinary Pair-Core Repair remains available','geometry allocates enhanced inside-spread/Active execution only','no hard 1/3 runtime threshold','no winner/PnL runtime input','no Floor safety reintroduction','max4 unchanged','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
