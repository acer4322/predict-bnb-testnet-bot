from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2;EPS=1e-9

class Cap1FavorableExpandRoutingSim(r28.FanoutRoleCapacitySim):
    """MS4-R2.24: route existing CAP1 Expand credit to a favorable legal live price.

    No new risk capacity and no pair veto.  Baseline CAP1's execution-first price remains
    untouched unless it is pair-incompatible with the current live opposite ECONOMIC_CORE
    and another unused current live level is pair-compatible.  In that case use the highest
    compatible level, preserving the most fillable price among favorable alternatives.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots)
        self.r224=Counter();self.r224Events=[]

    def _candidate_from_levels_v8(self,side,role,require_pair):
        if role!='SATELLITE_EXPAND':
            return super()._candidate_from_levels_v8(side,role,require_pair)
        baseline=super()._candidate_from_levels_v8(side,role,require_pair)
        if baseline is None:return None
        bp,bq,bproj,bsplit=baseline
        repair_side='DOWN' if side=='UP' else 'UP'
        core=self._core_for_side(repair_side)
        if core is None or core[2] is None:
            self.r224['NO_LIVE_CORE_BASELINE']+=1
            return baseline
        cp=float(core[2]['price'])
        if float(bp)+cp<=1.0+EPS:
            self.r224['BASELINE_ALREADY_FAVORABLE']+=1
            return baseline
        used=self._used_prices(side)
        levels=[float(v2.kprice(p)) for p in self._live_price_levels(side) if v2.kprice(p) not in used]
        compatible=[p for p in levels if float(p)+cp<=1.0+EPS]
        if not compatible:
            self.r224['NO_FAVORABLE_ALTERNATIVE_BASELINE']+=1
            return baseline
        p=max(compatible);q=1.0/float(p)
        if abs(float(p)-float(bp))<=EPS:
            self.r224['FAVORABLE_SAME_AS_BASELINE']+=1
            return baseline
        self.r224['FAVORABLE_REROUTE']+=1
        self.r224Events.append({'event':'FAVORABLE_EXPAND_REROUTE_CANDIDATE','generation':int(self.scopeGeneration),
                                'side':side,'coreRepairPrice':cp,'baselineExpandPrice':float(bp),
                                'baselinePairSum':float(bp)+cp,'routedExpandPrice':float(p),
                                'routedPairSum':float(p)+cp,'baselineQty':float(bq),'routedQty':float(q)})
        return float(p),float(q),None,None

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before=len(self.r224Events)
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok and role=='SATELLITE_EXPAND':
            repair_side='DOWN' if side=='UP' else 'UP';core=self._core_for_side(repair_side)
            cp=float(core[2]['price']) if core is not None and core[2] is not None else None
            ev={'t':int(t),'event':'CAP1_EXPAND_SUBMIT_AFTER_R224_ROUTING','generation':int(self.scopeGeneration),
                'side':side,'expandPrice':float(p),'qty':float(q),'coreRepairPrice':cp,
                'pairSum':(None if cp is None else float(p)+cp)}
            self.r224Events.append(ev);self.slot_history.append(ev)
        return ok

    def run_r224(self,w):
        r=super().run_cap(w)
        r['r224Stats']=dict(self.r224);r['r224Events']=self.r224Events[:2500]
        r['favorableExpandReroutes']=int(self.r224.get('FAVORABLE_REROUTE',0))
        return r


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r224_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=Cap1FavorableExpandRoutingSim(tape,4)
            try:c=sim.run_r224(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                     {'marketId':mid,'cell':'MS4_R224_CAP1_FAVORABLE_EXPAND_ROUTING','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'submits':b['submits'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                              'candidate':{'fills':c['fillEvents'],'submits':c['submits'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],
                                           'reroutes':c['favorableExpandReroutes'],'stats':c['r224Stats']},
                              'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'}
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R224_CAP1_FAVORABLE_EXPAND_ROUTING'}
        cmp=[]
        for m in mids:
            b,c=B[m],C[m]
            cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,
                        'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],
                        'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'favorableReroutes':c['favorableExpandReroutes']})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS for m in mids)
        anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        exercised=sum(C[m]['favorableExpandReroutes'] for m in mids)>0
        out={'version':'MS4_R2_24_CAP1_FAVORABLE_EXPAND_ROUTING_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,
             'rows':rows,'comparisonVsCap1':cmp,
             'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti,'favorableRerouteExercised':exercised},
             'boundary':['CAP1 risk capacity and ordinary admission frozen','only SATELLITE_EXPAND legal live-price ordering may change','if CAP1 baseline Expand price already pair-compatible with live opposite ECONOMIC_CORE, keep exact baseline','if baseline is incompatible but a current unused live favorable alternative exists, choose highest compatible price','if no compatible alternative or no live Core, keep exact baseline','no universal pair veto','no new risk budget','Repair/Overflow/Active/fanout unchanged','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output)
        op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
