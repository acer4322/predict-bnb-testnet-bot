from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_18_recoverability_backed_surplus.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r218',_STAGED);r218=importlib.util.module_from_spec(sp);sp.loader.exec_module(r218)
else:
    import tools.run_eth_ms4_r2_18_recoverability_backed_surplus as r218
r28=r218.r28;v2=r218.v2;EPS=1e-9

class RecoverabilityRepaymentEpochSim(r218.RecoverabilityBackedSurplusSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.r219=Counter();self.borrowAnchor=None;self.borrowSourceKey=None;self.borrowMetaByKey={};self.borrowSeenCum={};self.r219events=[]
    def _expand_authority(self,t,side,p,q):
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after);real=float(self._available_expand_risk_credit())
        if real+EPS>=risk:return {'ok':True,'source':'REALIZED_CREDIT','riskCost':risk,'realCredit':real,'recoverability':None}
        if self.borrowAnchor is not None:
            self.r219['RECOVERABILITY_BORROW_UNPAID_BLOCK']+=1
            return {'ok':False,'source':'RECOVERABILITY_BORROW_UNPAID','riskCost':risk,'realCredit':real,'recoverability':None}
        return super()._expand_authority(t,side,p,q)
    def _submit_expand_with_authority(self,t,side,p,q,proj,auth,event_name):
        before_n=self.n;ok=super()._submit_expand_with_authority(t,side,p,q,proj,auth,event_name)
        if not ok:return False
        if auth.get('source')=='RECOVERABILITY_BACKED':
            key=f'{side}_{before_n}';rec=auth.get('recoverability') or {};anchor=float(rec.get('floorBefore',self._physical_floor()))
            self.borrowMetaByKey[key]={'anchor':anchor,'generation':int(self.key_scope_gen.get(key,self.scopeGeneration)),'submitT':int(t),'side':side,'price':float(p),'qty':float(q),'forwardPairSum':rec.get('forwardPairSum')};self.borrowSeenCum[key]=0.0
            self.r219['BORROW_ORDER_SUBMIT']+=1
        return True
    def _any_live_borrow_order(self):
        for k in self.borrowMetaByKey:
            o=self.orders.get(k)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False
    def process(self,t):
        super().process(t)
        for k,m in list(self.borrowMetaByKey.items()):
            o=self.orders.get(k)
            if not o:continue
            cur=float(o.get('cum') or 0.0);old=float(self.borrowSeenCum.get(k,0.0))
            if cur>old+EPS:
                self.borrowSeenCum[k]=cur
                if self.borrowAnchor is None:
                    self.borrowAnchor=float(m['anchor']);self.borrowSourceKey=k;self.r219['BORROW_DEBT_BORN_ON_FILL']+=1
                    ev={'t':int(t),'event':'RECOVERABILITY_BORROW_DEBT_BORN','key':k,'anchorFloor':self.borrowAnchor,'physicalFloor':float(self._physical_floor()),'fillCum':cur,'forwardPairSum':m.get('forwardPairSum')};self.r219events.append(ev);self.slot_history.append(ev)
        if self.borrowAnchor is not None and (not self._any_live_borrow_order()) and float(self._physical_floor())>=float(self.borrowAnchor)-EPS:
            self.r219['BORROW_REPAID_BY_ACTUAL_FLOOR_RECOVERY']+=1
            ev={'t':int(t),'event':'RECOVERABILITY_BORROW_REPAID','sourceKey':self.borrowSourceKey,'anchorFloor':float(self.borrowAnchor),'physicalFloor':float(self._physical_floor())};self.r219events.append(ev);self.slot_history.append(ev)
            self.borrowAnchor=None;self.borrowSourceKey=None
    def run_r219(self,w):
        r=super().run_r218(w);r['r219Stats']=dict(self.r219);r['r219Events']=self.r219events[:1200];r['borrowAnchorTerminal']=self.borrowAnchor;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r219_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=RecoverabilityRepaymentEpochSim(tape,4)
            try:c=sim.run_r219(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},{'marketId':mid,'cell':'MS4_R219_RECOVERABILITY_REPAYMENT_EPOCH','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},'candidate':{'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'recSubs':c['recoverabilityBackedExpandSubmits'],'r219':c['r219Stats'],'terminalBorrow':c['borrowAnchorTerminal']},'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R219_RECOVERABILITY_REPAYMENT_EPOCH'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'recoverabilityBackedExpandSubmits':c['recoverabilityBackedExpandSubmits'],'repayments':c.get('r219Stats',{}).get('BORROW_REPAID_BY_ACTUAL_FLOOR_RECOVERY',0)})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS for m in mids);anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_19_RECOVERABILITY_REPAYMENT_EPOCH_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['R2.18 recoverability-backed Expand authority retained','a recoverability-backed order may fill its preregistered full venue-min qty, but after any confirmed fill no NEW recoverability-backed Expand may be born until that order is terminal and actual physical Floor has recovered to the exact pre-Expand Floor anchor','realized-credit Expand remains unaffected','Core/Repair/CAP1 fanout/ordinary Expand/4-slot behavior remains live while borrow debt is unpaid','no pairSum threshold; forward pair economics remains diagnostic only','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
