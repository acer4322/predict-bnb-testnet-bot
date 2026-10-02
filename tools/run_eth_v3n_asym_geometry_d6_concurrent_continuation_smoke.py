from __future__ import annotations
import argparse,collections,importlib.util,json,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v3m_local',HERE/'run_eth_v3m_asymmetric_geometry_enhanced_repair_smoke.py')
v3m=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3m)
v3b=v3m.v3b;EPS=v3m.EPS

class V3N(v3m.AsymmetricGeometryEnhancedRepairV3M):
    def __init__(self,tape):
        super().__init__(tape);self.v3n_counter=collections.Counter();self.v3n_events=[];self._v3n_after_fallback=None

    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        before_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        after_fb=int(self.asym_counter.get('passiveGeometryFallbacks',0))
        a=self.q_arm
        if after_fb>before_fb and cand is not None and a is not None:
            self._v3n_after_fallback={'t':int(a['t']),'qv':a['qv'],'repairSide':side,'repairRole':a['role'],'responsibilityId':int(a['responsibilityId'])}
        return cand

    def _try_concurrent_expand(self,t,qv,source):
        if len(self.slot_key)>=self.max_slots:
            self.v3n_counter['noFreeSlot']+=1;return False
        side,role,_,_=v3b.base.MinimalPairRoleSim._role_decision(self,qv)
        if role!='SATELLITE_EXPAND':
            self.v3n_counter[f'roleNotExpand:{role}']+=1;return False
        cand=v3b.base.MinimalPairRoleSim._candidate_from_levels(self,side,True,False)
        if cand is None:
            self.v3n_counter['noPairLegalExpandCandidate']+=1;return False
        p,q,proj=cand
        ok=v3b.base.MinimalPairRoleSim._submit_role(self,int(t),side,role,float(p),float(q),proj,'V3N_ASYM_GEOM_D6_CONCURRENT_EXPAND')
        if ok:
            self.v3n_counter['concurrentExpandSubmits']+=1
            self.v3n_events.append({'event':'V3N_CONCURRENT_EXPAND_SUBMIT','t':int(t),'side':side,'role':role,'price':float(p),'qty':float(q),'sourceResponsibilityId':source['responsibilityId'],'repairSide':source['repairSide']})
        else:self.v3n_counter['concurrentExpandSubmitFailed']+=1
        return ok

    def _submit_role(self,t,side,role,p,q,proj,source):
        pending=self._v3n_after_fallback
        self._v3n_after_fallback=None
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok and pending is not None and side==pending['repairSide'] and role==pending['repairRole']:
            self.v3n_counter['ordinaryFallbackRepairSubmitted']+=1
            self._try_concurrent_expand(t,pending['qv'],pending)
        return ok

    def run_v3n(self,winner='__UNSCORED__'):
        r=super().run_v3m(winner);r['v3nCounters']=dict(self.v3n_counter);r['v3nEvents']=self.v3n_events[:5000];r['v3nAsymConcurrentContinuation']=True;return r

def metrics(rows,cell):return v3m.metrics(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3n_asym_d6_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls,fn in [('A_V3B',v3b.FifoAggregateResponsibilityLadderV3B,'run_qty'),('B_V3M',v3m.AsymmetricGeometryEnhancedRepairV3M,'run_v3m'),('C_V3N',V3N,'run_v3n')]:
                sim=Cls(tape)
                try:r=getattr(sim,fn)('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);rows.append({'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r})
            A,B,C=rows[-3:]
            print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':A['pnlDiagnosticOnly'],'fills':A['fillEvents'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'fills':B['fillEvents'],'alts':B['fillSideAlternations']},'C':{'pnl':C['pnlDiagnosticOnly'],'floor':C['floor'],'best':C['best'],'fills':C['fillEvents'],'alts':C['fillSideAlternations'],'v3n':C.get('v3nCounters'),'ledgerViolations':C['quantityLedgerSummary'].get('invariantViolations')}},ensure_ascii=False),flush=True)
    out={'version':'V3N_ASYM_GEOMETRY_D6_CONCURRENT_CONTINUATION_SMOKE','date':'2026-09-06','researchOnly':True,'markets':mids,'rows':rows,'summary':{c:metrics(rows,c) for c in ['A_V3B','B_V3M','C_V3N']},'boundary':['V3M geometry allocator unchanged','after ordinary fallback Repair submit, at most one same-receipt SATELLITE_EXPAND if frozen Pair-Core role decision selects Expand','max4 unchanged','no extra Repair child from V3N','no winner/PnL runtime authority','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
