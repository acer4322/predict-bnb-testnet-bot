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
EPS=1e-9

class ActiveEconomicRearmSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots)
        self.ecoUn={'UP':[],'DOWN':[]};self.activeEconomicDebt=0.0;self.totalActivePairDebtBorn=0.0;self.totalCoreReservePaid=0.0;self.r212=Counter();self.r212events=[]
    def _try_active_drain(self,t:int):
        if self.activeEconomicDebt>EPS:
            self.r212['ACTIVE_ECONOMIC_DEBT_REARM_WAIT']+=1
            if not self.r212events or self.r212events[-1].get('t')!=int(t) or self.r212events[-1].get('event')!='ACTIVE_ECONOMIC_DEBT_REARM_WAIT':
                e={'t':int(t),'event':'ACTIVE_ECONOMIC_DEBT_REARM_WAIT','outstandingDebt':float(self.activeEconomicDebt),'scopeGeneration':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks)};self.r212events.append(e);self.slot_history.append(e)
            return False
        return super()._try_active_drain(t)
    def _pair_fill_into_economic_ledger(self,e):
        side=str(e['side']);opp='DOWN' if side=='UP' else 'UP';rem=float(e.get('fillInc') or 0.0);p=float(e['price']);key=e.get('key');role=str(e.get('role'))
        bad=good=paired=0.0;cost=0.0
        while rem>EPS and self.ecoUn[opp]:
            lot=self.ecoUn[opp][0];m=min(rem,float(lot['qty']));ps=float(lot['price'])+p;paired+=m;cost+=m*ps;bad+=m*max(0.0,ps-1.0);good+=m*max(0.0,1.0-ps);rem-=m;lot['qty']-=m
            if lot['qty']<=EPS:self.ecoUn[opp].pop(0)
        if rem>EPS:self.ecoUn[side].append({'qty':rem,'price':p})
        is_active=key in self.activeMeta
        if is_active and bad>EPS:
            self.activeEconomicDebt+=bad;self.totalActivePairDebtBorn+=bad;self.r212['ACTIVE_PAIR_DEBT_BORN']+=1
            x={'t':int(e['t']),'event':'ACTIVE_PAIR_DEBT_BORN','key':key,'pairDamage':bad,'weightedPairSum':cost/paired if paired>EPS else None,'outstandingDebtAfter':float(self.activeEconomicDebt)};self.r212events.append(x);self.slot_history.append(x)
        if role=='ECONOMIC_CORE' and good>EPS and self.activeEconomicDebt>EPS:
            paid=min(self.activeEconomicDebt,good);self.activeEconomicDebt-=paid;self.totalCoreReservePaid+=paid;self.r212['CORE_RESERVE_REPAYS_ACTIVE_DEBT']+=1
            x={'t':int(e['t']),'event':'CORE_RESERVE_REPAYS_ACTIVE_DEBT','key':key,'coreReserveGain':good,'paid':paid,'outstandingDebtAfter':float(self.activeEconomicDebt)};self.r212events.append(x);self.slot_history.append(x)
    def process(self,t):
        before=len(self.slot_history);super().process(t)
        for e in list(self.slot_history[before:]):
            if e.get('event')=='ROLE_FILL_SPLIT' and float(e.get('fillInc') or 0)>EPS:self._pair_fill_into_economic_ledger(e)
    def run_r212(self,w):
        r=super().run_cap(w);r['activeEconomicDebtOutstanding']=float(self.activeEconomicDebt);r['totalActivePairDebtBorn']=float(self.totalActivePairDebtBorn);r['totalCoreReservePaidToActiveDebt']=float(self.totalCoreReservePaid);r['r212Stats']=dict(self.r212);r['r212Events']=self.r212events[:1500];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r212_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:r0=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=ActiveEconomicRearmSim(tape,4)
            try:r=sim.run_r212(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R212_ACTIVE_ECONOMIC_REARM','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'cap1':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'active':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'r212':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'debtBorn':r['totalActivePairDebtBorn'],'corePaid':r['totalCoreReservePaidToActiveDebt'],'debtEnd':r['activeEconomicDebtOutstanding'],'stats':r['r212Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R212_ACTIVE_ECONOMIC_REARM'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'activeDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids);anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_12_ACTIVE_ECONOMIC_REARM_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['R2.8 CAP1 slots/Passive pricing/fanout unchanged','only repeated Active re-arm is economically accounted','confirmed Active Repair FIFO pair damage creates monetary economic debt','confirmed favorable ECONOMIC_CORE FIFO pair reserve pays that debt down exactly','no fixed pairSum/time/tick threshold','Passive/Core/fanout/Expand remain available while Active debt waits','no Target runtime inputs','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
