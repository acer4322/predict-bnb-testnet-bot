from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_19_recoverability_repayment_epoch.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r219',_STAGED);r219=importlib.util.module_from_spec(sp);sp.loader.exec_module(r219)
else:
    import tools.run_eth_ms4_r2_19_recoverability_repayment_epoch as r219
r28=r219.r28;EPS=1e-9

class OwnedCoveredSurplusSim(r219.RecoverabilityRepaymentEpochSim):
    def _expand_authority(self,t,side,p,q):
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after);real=float(self._available_expand_risk_credit())
        if real+EPS>=risk:return {'ok':True,'source':'REALIZED_CREDIT','riskCost':risk,'realCredit':real,'recoverability':None}
        if self.borrowAnchor is not None:
            self.r219['RECOVERABILITY_BORROW_UNPAID_BLOCK']+=1
            return {'ok':False,'source':'RECOVERABILITY_BORROW_UNPAID','riskCost':risk,'realCredit':real,'recoverability':None}
        if self._has_live_recoverable_expand():return {'ok':False,'source':'RECOVERABILITY_ALREADY_LIVE','riskCost':risk,'realCredit':real,'recoverability':None}
        rec=self._recoverability(side,p,q)
        if not rec.get('recoverable'):
            return {'ok':False,'source':'NOT_RECOVERABLE','riskCost':risk,'realCredit':real,'recoverability':rec}
        if rec.get('reason')!='OWNED_REPAIR_RESTORES_PREEXPAND_FLOOR':
            self.r219['FUTURE_DEPENDENT_RECOVERABILITY_NOT_SPENDABLE']+=1
            return {'ok':False,'source':'FUTURE_DEPENDENT_RECOVERABILITY','riskCost':risk,'realCredit':real,'recoverability':rec}
        self.r219['OWNED_COVERED_RECOVERABILITY_AUTHORIZED']+=1
        return {'ok':True,'source':'RECOVERABILITY_BACKED','riskCost':risk,'realCredit':real,'recoverability':rec}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r220_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=OwnedCoveredSurplusSim(tape,4)
            try:c=sim.run_r219(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},{'marketId':mid,'cell':'MS4_R220_OWNED_COVERED_SURPLUS','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},'candidate':{'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'recSubs':c['recoverabilityBackedExpandSubmits'],'r219':c['r219Stats'],'terminalBorrow':c['borrowAnchorTerminal']},'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R220_OWNED_COVERED_SURPLUS'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'recoverabilityBackedExpandSubmits':c['recoverabilityBackedExpandSubmits'],'repayments':c.get('r219Stats',{}).get('BORROW_REPAID_BY_ACTUAL_FLOOR_RECOVERY',0)})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS for m in mids);anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_20_OWNED_COVERED_SURPLUS_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 remains functional baseline','realized-credit Expand unchanged','credit-insufficient Expand may borrow only when already-live current-generation Repair reservations alone restore exact pre-Expand physical Floor under full-fill accounting','hypothetical future Repair is never spendable authority','after any borrow fill, no new borrow until actual physical Floor recovers to the pre-Expand anchor and borrow order is terminal','pairSum is diagnostic only, not hard admission','Core/Repair/fanout/ordinary Expand remain active','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
