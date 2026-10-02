from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_single_switch_counterfactual.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('cf1',_STAGED);cf1=importlib.util.module_from_spec(sp);sp.loader.exec_module(cf1)
else:
    import tools.run_eth_ms4_r2_single_switch_counterfactual as cf1
r21=cf1.r21;r1=cf1.r1;EPS=1e-9

class AllSatEnumerator(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,model):super().__init__(tape,model,4);self.eligible=[];self.nsat=0
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        pure=role=='SATELLITE_REPAIR' and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q)
        if pure:
            score,diag=self._score(side,role,p,q,split)
            if score<0.5:
                self.nsat+=1;qv=r1.v2.base.quotes(self.book);ask=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None;opp='DOWN' if side=='UP' else 'UP';oa=self.unmatched_avg(opp)
                self.eligible.append({'occurrence':self.nsat,'t':int(t),'side':side,'role':role,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'activeAsk':ask,'activePremium':(ask-float(p)) if ask is not None else None,'passivePairSum':(float(oa)+float(p)) if oa is not None else None,'activePairSum':(float(oa)+ask) if oa is not None and ask is not None else None,'floorBefore':float(self._physical_floor()),'debtQty':float(self._scope_debt_qty()),'repairDebtFraction':float(q)/max(float(self._scope_debt_qty()),EPS),'scopeGeneration':int(self.scopeGeneration),'availableRiskCredit':float(self._available_expand_risk_credit()),'liveSlots':int(len(self.slot_key))})
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def run_enum(self,w):r=r1.QueueAwareRepairRoutingSim.run_v88(self,w);r['allSatelliteOpportunities']=self.eligible;return r

class NthSatSwitch(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,model,nth):super().__init__(tape,model,4);self.nth=int(nth);self.seen=0;self.selected=None;self.succeeded=False
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        pure=role=='SATELLITE_REPAIR' and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q)
        if pure:
            score,diag=self._score(side,role,p,q,split)
            if score<0.5:
                self.seen+=1
                if self.seen==self.nth:
                    qv=r1.v2.base.quotes(self.book);ask=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None;opp='DOWN' if side=='UP' else 'UP';oa=self.unmatched_avg(opp)
                    self.selected={'occurrence':self.nth,'t':int(t),'side':side,'role':role,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'activeAsk':ask,'activePremium':(ask-float(p)) if ask is not None else None,'passivePairSum':(float(oa)+float(p)) if oa is not None else None,'activePairSum':(float(oa)+ask) if oa is not None and ask is not None else None,'floorBefore':float(self._physical_floor()),'debtQty':float(self._scope_debt_qty()),'repairDebtFraction':float(q)/max(float(self._scope_debt_qty()),EPS),'scopeGeneration':int(self.scopeGeneration),'availableRiskCredit':float(self._available_expand_risk_credit()),'liveSlots':int(len(self.slot_key))}
                    if self._submit_active(t,side,role,q,score,diag):self.succeeded=True;return True
                    self.selected['activeSwitchBlocked']=True
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def run_n(self,w):r=super().run_r21(w);r['selected']=self.selected;r['switchSucceeded']=self.succeeded;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='ms4_r2_allsatcf_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            tape=tmp/'tapes'/f'{mid}.json.xz';winner=co[mid]['winner'];ctl=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(winner)
            finally:ctl.close()
            en=AllSatEnumerator(tape,model)
            try:er=en.run_enum(winner)
            finally:en.close()
            opps=er.get('allSatelliteOpportunities',[]);print(json.dumps({'marketId':mid,'eligibleSatellite':len(opps),'controlFills':r0['fillEvents'],'controlPnl':r0['pnlDiagnosticOnly'],'controlFloor':r0['floor']},ensure_ascii=False),flush=True)
            for o in opps:
                sim=NthSatSwitch(tape,model,o['occurrence'])
                try:r=sim.run_n(winner)
                finally:sim.close()
                z={'marketId':mid,'occurrence':o['occurrence'],'switchSucceeded':r.get('switchSucceeded',False),'opportunity':r.get('selected') or o,'controlSubmits':r0['submits'],'candidateSubmits':r['submits'],'submitDelta':r['submits']-r0['submits'],'controlFills':r0['fillEvents'],'candidateFills':r['fillEvents'],'fillDelta':r['fillEvents']-r0['fillEvents'],'controlPnl':r0['pnlDiagnosticOnly'],'candidatePnl':r['pnlDiagnosticOnly'],'pnlDelta':r['pnlDiagnosticOnly']-r0['pnlDiagnosticOnly'],'controlFloor':r0['floor'],'candidateFloor':r['floor'],'floorDelta':r['floor']-r0['floor'],'activeFillQty':r.get('ms4R2ActiveRepairFillQty',0.0),'unauthorizedOverflowQty':r['unauthorizedOverflowQty'],'repairQuotaExcessMax':r['repairQuotaExcessMax']};rows.append(z);print(json.dumps(z,ensure_ascii=False),flush=True)
        valid=[x for x in rows if x['switchSucceeded']];out={'version':'MS4_R2_ALL_LOW_FILL_SATELLITE_SINGLE_SWITCH_COUNTERFACTUAL_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'counterfactuals':rows,'aggregate':{'attempts':len(rows),'switchSucceeded':len(valid),'positivePnlDelta':sum(x['pnlDelta']>EPS for x in valid),'nonnegativeFloorDelta':sum(x['floorDelta']>=-EPS for x in valid),'meanPnlDelta':sum(x['pnlDelta'] for x in valid)/len(valid) if valid else None,'meanFloorDelta':sum(x['floorDelta'] for x in valid)/len(valid) if valid else None,'meanFillDelta':sum(x['fillDelta'] for x in valid)/len(valid) if valid else None},'gates':{'correctnessPass':all(x['unauthorizedOverflowQty']<=EPS and x['repairQuotaExcessMax']<=EPS for x in rows),'antiCollapse50pctPass':all(x['candidateFills']>=.5*x['controlFills'] for x in rows if x['controlFills']>0)},'boundary':['all baseline low-fill pure SATELLITE_REPAIR opportunities in consumed dev6','one Active switch per replay, then frozen MS4-R1','same Repair qty, zero Overflow, no new debt/risk authority','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
