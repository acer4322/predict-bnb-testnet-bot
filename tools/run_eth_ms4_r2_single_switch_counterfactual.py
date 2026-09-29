from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_1_fillability_active_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r21',_STAGED);r21=importlib.util.module_from_spec(sp);sp.loader.exec_module(r21)
else:
    import tools.run_eth_ms4_r2_1_fillability_active_repair as r21
r1=r21.r1;EPS=1e-9

class OpportunityEnumerator(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,model):
        super().__init__(tape,model,4);self.eligible=[];self.roleSeen={}
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        pure=role in ('ECONOMIC_CORE','SATELLITE_REPAIR') and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q)
        if pure:
            score,diag=self._score(side,role,p,q,split)
            if score<0.5:
                n=self.roleSeen.get(role,0)+1;self.roleSeen[role]=n
                if n==1:
                    qv=r1.v2.base.quotes(self.book);ask=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None
                    opp='DOWN' if side=='UP' else 'UP';oa=self.unmatched_avg(opp)
                    self.eligible.append({'role':role,'occurrence':1,'t':int(t),'side':side,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'activeAsk':ask,'activePremium':(ask-float(p)) if ask is not None else None,'passivePairSum':(float(oa)+float(p)) if oa is not None else None,'activePairSum':(float(oa)+ask) if oa is not None and ask is not None else None,'floorBefore':float(self._physical_floor()),'debtQty':float(self._scope_debt_qty()),'repairDebtFraction':float(q)/max(float(self._scope_debt_qty()),EPS),'scopeGeneration':int(self.scopeGeneration)})
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def run_enum(self,winner):
        r=r1.QueueAwareRepairRoutingSim.run_v88(self,winner);r['selectedOpportunities']=self.eligible;return r

class SingleSwitchSim(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,model,target_role):
        super().__init__(tape,model,4);self.targetRole=target_role;self.targetSeen=0;self.selected=None;self.switchAttempted=False;self.switchSucceeded=False
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        pure=role in ('ECONOMIC_CORE','SATELLITE_REPAIR') and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q)
        if pure:
            score,diag=self._score(side,role,p,q,split)
            if score<0.5 and role==self.targetRole:
                self.targetSeen+=1
                if self.targetSeen==1 and not self.switchAttempted:
                    self.switchAttempted=True
                    qv=r1.v2.base.quotes(self.book);ask=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None
                    opp='DOWN' if side=='UP' else 'UP';oa=self.unmatched_avg(opp);before=float(self._physical_floor())
                    self.selected={'role':role,'t':int(t),'side':side,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'activeAsk':ask,'activePremium':(ask-float(p)) if ask is not None else None,'passivePairSum':(float(oa)+float(p)) if oa is not None else None,'activePairSum':(float(oa)+ask) if oa is not None and ask is not None else None,'floorBefore':before,'debtQty':float(self._scope_debt_qty()),'repairDebtFraction':float(q)/max(float(self._scope_debt_qty()),EPS),'scopeGeneration':int(self.scopeGeneration)}
                    if self._submit_active(t,side,role,q,score,diag):
                        self.switchSucceeded=True;return True
                    self.selected['activeSwitchBlocked']=True
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def run_single(self,winner):
        r=super().run_r21(winner);r['singleSwitchSelected']=self.selected;r['singleSwitchAttempted']=self.switchAttempted;r['singleSwitchSucceeded']=self.switchSucceeded;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='ms4_r2_singlecf_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[];cf=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            en=OpportunityEnumerator(tape,model)
            try:re=en.run_enum(cr['winner'])
            finally:en.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0,'selectedOpportunities':re.get('selectedOpportunities',[])})
            roles=[x['role'] for x in re.get('selectedOpportunities',[])]
            for role in roles:
                sim=SingleSwitchSim(tape,model,role)
                try:r=sim.run_single(cr['winner'])
                finally:sim.close()
                sel=r.get('singleSwitchSelected') or {}
                z={'marketId':mid,'targetRole':role,'switchAttempted':r.get('singleSwitchAttempted'),'switchSucceeded':r.get('singleSwitchSucceeded'),'opportunity':sel,'controlSubmits':r0['submits'],'candidateSubmits':r['submits'],'submitDelta':r['submits']-r0['submits'],'controlFills':r0['fillEvents'],'candidateFills':r['fillEvents'],'fillDelta':r['fillEvents']-r0['fillEvents'],'controlPnl':r0['pnlDiagnosticOnly'],'candidatePnl':r['pnlDiagnosticOnly'],'pnlDelta':r['pnlDiagnosticOnly']-r0['pnlDiagnosticOnly'],'controlFloor':r0['floor'],'candidateFloor':r['floor'],'floorDelta':r['floor']-r0['floor'],'activeFillQty':r.get('ms4R2ActiveRepairFillQty',0.0),'unauthorizedOverflowQty':r['unauthorizedOverflowQty'],'repairQuotaExcessMax':r['repairQuotaExcessMax']}
                cf.append(z);print(json.dumps(z,ensure_ascii=False),flush=True)
        valid=[x for x in cf if x['switchSucceeded']]
        out={'version':'MS4_R2_SINGLE_SWITCH_COUNTERFACTUAL_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'controls':rows,'counterfactuals':cf,'aggregate':{'attempts':len(cf),'switchSucceeded':sum(bool(x['switchSucceeded']) for x in cf),'meanPnlDelta':sum(x['pnlDelta'] for x in valid)/len(valid) if valid else None,'meanFloorDelta':sum(x['floorDelta'] for x in valid)/len(valid) if valid else None,'meanFillDelta':sum(x['fillDelta'] for x in valid)/len(valid) if valid else None,'positivePnlDelta':sum(x['pnlDelta']>EPS for x in valid),'nonnegativeFloorDelta':sum(x['floorDelta']>=-EPS for x in valid)},'gates':{'correctnessPass':all(x['unauthorizedOverflowQty']<=EPS and x['repairQuotaExcessMax']<=EPS for x in cf),'antiCollapse50pctPass':all(x['candidateFills']>=0.5*x['controlFills'] for x in cf if x['controlFills']>0)},'boundary':['one intervention only per replay','earliest low-fill pure Repair opportunity per role/market','after intervention revert to frozen MS4-R1','same Repair qty; zero Overflow; no new debt/risk authority','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
