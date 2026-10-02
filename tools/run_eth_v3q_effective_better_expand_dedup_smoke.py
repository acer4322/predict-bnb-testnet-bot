from __future__ import annotations
import argparse,collections,importlib.util,json,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('v3p_local',HERE/'run_eth_v3p_ordinary_repair_plus_expand_package_smoke.py')
v3p=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3p)
v3m=v3p.v3m;v3b=v3p.v3b;EPS=v3p.EPS

class V3Q(v3p.V3P):
    def __init__(self,tape):
        super().__init__(tape);self.v3q_counter=collections.Counter();self.v3q_events=[]
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        cand=super()._candidate_from_levels(side,require_pair,require_budget)
        p=self._pkg_pending
        if p is None:return cand
        better=[]
        for _,key,o,role in self._live_role_rows('SATELLITE_EXPAND',p['expandSide']):
            if not o or bool(o.get('cancelRequested')):continue
            if float(o.get('price') or 0.0)>float(p['expandPrice'])+EPS:
                better.append({'key':key,'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0)})
        if better:
            self.v3q_counter['effectiveBetterExpandBlocks']+=1
            self.v3q_events.append({'event':'V3Q_EFFECTIVE_BETTER_EXPAND_BLOCK','t':int(p['t']),'expandSide':p['expandSide'],'proposedPrice':float(p['expandPrice']),'better':better,'responsibilityId':p['responsibilityId']})
            # Suppress only the additional package Expand. Ordinary Repair candidate remains unchanged.
            self._pkg_pending=None
        else:self.v3q_counter['packagePassNoBetterExpand']+=1
        return cand
    def run_v3q(self,w='__UNSCORED__'):
        r=super().run_v3p(w);r['v3qCounters']=dict(self.v3q_counter);r['v3qEvents']=self.v3q_events[:5000];r['v3qEffectiveBetterExpandDedup']=True;return r

def metrics(rows,cell):return v3m.metrics(rows,cell)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3q_dedup_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls,fn in [('A_V3M',v3m.AsymmetricGeometryEnhancedRepairV3M,'run_v3m'),('B_V3P',v3p.V3P,'run_v3p'),('C_V3Q',V3Q,'run_v3q')]:
                sim=Cls(tape)
                try:r=getattr(sim,fn)('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);rows.append({'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r})
            A,B,C=rows[-3:]
            print(json.dumps({'progress':i,'marketId':mid,'A':{'pnl':A['pnlDiagnosticOnly'],'fills':A['fillEvents'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'fills':B['fillEvents'],'alts':B['fillSideAlternations']},'C':{'pnl':C['pnlDiagnosticOnly'],'floor':C['floor'],'best':C['best'],'fills':C['fillEvents'],'alts':C['fillSideAlternations'],'maxSlots':C['maxSimultaneousSlots'],'v3p':C.get('v3pCounters'),'v3q':C.get('v3qCounters'),'ledgerViolations':C['quantityLedgerSummary'].get('invariantViolations')}},ensure_ascii=False),flush=True)
    out={'version':'V3Q_EFFECTIVE_BETTER_EXPAND_DEDUP_SMOKE','date':'2026-09-06','researchOnly':True,'markets':mids,'rows':rows,'summary':{c:metrics(rows,c) for c in ['A_V3M','B_V3P','C_V3Q']},'boundary':['V3P package geometry unchanged','only additional package Expand suppressed when same-side non-cancel-pending SATELLITE_EXPAND already exists at strictly better price','ordinary Repair always preserved','no max5/cancel/slot replacement','no winner/PnL runtime authority','realistic HFT','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
