from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_47_bounded_core_service_favorable_recycle as r247
v2=r247.v2; EPS=1e-9; REPAIR_ROLES=r247.REPAIR_ROLES

class PreRepairRiskTrancheSim(r247.BoundedCoreServiceFavorableRecycleSim):
    """R2.55: one explicit atomic risk-bearing same-direction Expand before Repair.

    The tranche is NOT Repair credit. It may temporarily make native committed
    continuation risk exceed scopeRiskCreditTotal. That excess is explicit risk debt.
    Confirmed Repair credit then reduces the deficit naturally before generic monetary
    continuation becomes spendable again. Favorable one-use Repair-lot authority remains
    independent and may still re-expand while monetary risk debt exists.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r255=Counter(); self.r255Events=[]
        self.riskTrancheKeys=set(); self.riskTrancheMeta={}; self.riskTrancheGenerationUsed=set()
        self.riskTrancheAuthorizedRisk=0.0; self.riskTrancheSpentRisk=0.0; self.riskTrancheReleasedRisk=0.0
        self.riskTrancheOverrunMax=0.0; self.riskDebtPeak=0.0
        self.passiveRepairSubmitsAfterRiskTranche=0; self.passiveRepairFillsAfterRiskTranche=0
        self.firstRepairAfterRiskTrancheMs=None; self.sameGenerationRepairAfterRiskTranche=False

    def _risk_authority_current_generation(self):
        g=int(self.scopeGeneration)
        return sum(float(x['held'])+float(x['spent']) for x in self.riskTrancheMeta.values()
                   if int(x['generation'])==g)

    def _risk_debt_outstanding(self):
        if self.scopeSide is None:return 0.0
        committed=(float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+
                   float(self._service_claim()))
        return max(0.0,committed-float(self.scopeRiskCreditTotal))

    def _audit_r247(self):
        # Preserve exact R2.47 conservation checks, but the single explicit risk tranche
        # is a separate authority source and may cover temporary credit deficit.
        for x in self.serviceLedger.values():
            err=abs(float(x['authorized'])-float(x['held'])-float(x['spent'])-float(x['released']))
            self.serviceChecks['serviceConservationErrorMax']=max(self.serviceChecks['serviceConservationErrorMax'],err)
        if self.scopeSide is not None:
            committed=float(self.scopeRiskCreditConsumed)+float(self._reserved_current_expand_risk())+self._service_claim()
            allowance=float(self.scopeRiskCreditTotal)+float(self._risk_authority_current_generation())
            self.serviceChecks['combinedAuthorityExcessMax']=max(
                self.serviceChecks['combinedAuthorityExcessMax'],max(0.0,committed-allowance))
        for x in self.riskTrancheMeta.values():
            err=abs(float(x['authorized'])-float(x['held'])-float(x['spent'])-float(x['released']))
            self.riskTrancheOverrunMax=max(self.riskTrancheOverrunMax,err)
        debt=self._risk_debt_outstanding(); self.riskDebtPeak=max(self.riskDebtPeak,debt)
        # Debt itself is allowed only up to current-generation explicit risk authority.
        self.riskTrancheOverrunMax=max(self.riskTrancheOverrunMax,
            max(0.0,debt-float(self._risk_authority_current_generation())))

    def _available_core_service_authority(self):
        # Explicit risk debt must be serviced by Repair before another expensive Core rescue.
        if self._risk_debt_outstanding()>EPS:return 0.0
        return super()._available_core_service_authority()

    def _try_pre_repair_risk_tranche(self,t,end):
        if self.scopeSide is None or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return False
        gen=int(self.scopeGeneration)
        if gen in self.riskTrancheGenerationUsed:return False
        if int(self.scopeRepairProgressClocks)!=0:return False
        if self._has_stale_scope_reservation():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand
        before_floor=float(self._physical_floor()); after_floor=float(self._candidate_alone_floor(side,p,q))
        risk=max(0.0,before_floor-after_floor)
        if risk<=EPS:return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):return False
        key=f'{side}_{before_n}'
        self.riskTrancheKeys.add(key); self.riskTrancheGenerationUsed.add(gen)
        self.riskTrancheMeta[key]={'key':key,'generation':gen,'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(p),'qty':float(q),'authorized':float(risk),
            'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':float(super()._available_expand_risk_credit()),
            'repairProgressAtSubmit':int(self.scopeRepairProgressClocks)}
        self.riskTrancheAuthorizedRisk+=risk; self.r255['RISK_TRANCHE_SUBMIT']+=1
        ev={'t':int(t),'event':'R255_PRE_REPAIR_RISK_TRANCHE_SUBMIT',**self.riskTrancheMeta[key],
            'physicalFloorBefore':before_floor,'candidateFloor':after_floor,
            'riskDebtBefore':float(self._risk_debt_outstanding())}
        self.r255Events.append(ev); self.slot_history.append(ev)
        # Risk and passive Repair are intentionally allowed to coexist. Do not wait for fill.
        made=self._parallel_repair_fill(t,self._repair_side())
        self.passiveRepairSubmitsAfterRiskTranche+=int(made)
        if made:self.r255['PASSIVE_REPAIR_SUBMIT_SAME_RECEIPT_AFTER_RISK']+=int(made)
        self._audit_r247(); return True

    def _open_one_option(self,t,qv,end):
        # Frozen R2.47 ordinary action/replenishment runs first, but a free physical slot may
        # also carry the explicit pre-Repair risk tranche on the same receipt.
        super()._open_one_option(t,qv,end)
        self._try_pre_repair_risk_tranche(t,end)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key')); gen=int(ev.get('generationAtSubmit') or -1)
            inc=float(ev.get('fillInc') or 0.0); orisk=float(ev.get('overflowRisk') or 0.0)
            rq=float(ev.get('repairAllocated') or 0.0); role=str(ev.get('role')); side=str(ev.get('side'))
            if key in self.riskTrancheMeta and inc>EPS:
                x=self.riskTrancheMeta[key]
                take=min(float(x['held']),orisk)
                x['held']-=take; x['spent']+=orisk
                self.riskTrancheSpentRisk+=orisk; self.r255['RISK_TRANCHE_FILL_EVENTS']+=1
                self.r255['RISK_TRANCHE_FILL_QTY_MILLI']+=int(round(inc*1000))
                self.r255Events.append({'t':int(t),'event':'R255_RISK_TRANCHE_FILL','key':key,
                    'fillQty':inc,'realizedRisk':orisk,'heldAfter':x['held'],'spentAfter':x['spent'],
                    'riskDebtAfter':float(self._risk_debt_outstanding())})
            # Passive Repair only: activeMeta keys are excluded.
            if (rq>EPS and role in REPAIR_ROLES and key not in self.activeMeta and gen in self.riskTrancheGenerationUsed):
                seed_times=[int(x['submittedAt']) for x in self.riskTrancheMeta.values() if int(x['generation'])==gen]
                if seed_times and int(t)>=min(seed_times):
                    self.passiveRepairFillsAfterRiskTranche+=1; self.sameGenerationRepairAfterRiskTranche=True
                    lag=int(t)-min(seed_times)
                    if self.firstRepairAfterRiskTrancheMs is None or lag<self.firstRepairAfterRiskTrancheMs:
                        self.firstRepairAfterRiskTrancheMs=lag
                    self.r255['PASSIVE_REPAIR_FILL_AFTER_RISK']+=1
                    self.r255Events.append({'t':int(t),'event':'R255_PASSIVE_REPAIR_FILL_AFTER_RISK',
                        'key':key,'generation':gen,'repairQty':rq,'side':side,'lagFromRiskSubmitMs':lag,
                        'riskDebtAfterRepair':float(self._risk_debt_outstanding())})
        # Release unused risk authority only after native terminal reconciliation.
        for key,x in self.riskTrancheMeta.items():
            if x['terminal'] is not None:continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES or key in self.slot_key.values():continue
            if float(x['held'])>EPS:
                self.riskTrancheReleasedRisk+=float(x['held']); x['released']+=float(x['held']); x['held']=0.0
            x['terminal']=st
            self.r255Events.append({'t':int(t),'event':'R255_RISK_TRANCHE_TERMINAL','key':key,
                'status':st,'spent':x['spent'],'released':x['released']})
        self._audit_r247()

    def run_r255(self,winner):
        r=super().run_r247(winner); self._audit_r247()
        correct=(bool(r.get('r247ServiceCorrectnessPass')) and self.riskTrancheOverrunMax<=EPS and
                 float(r.get('unauthorizedOverflowQty',0.0))<=EPS and float(r.get('repairQuotaExcessMax',0.0))<=EPS)
        r.update({'r255Version':'MS4_R2_55_PRE_REPAIR_RISK_TRANCHE_V1',
            'r255Stats':dict(self.r255),'r255Events':self.r255Events[:3000],
            'riskTrancheSubmits':int(self.r255.get('RISK_TRANCHE_SUBMIT',0)),
            'riskTrancheFillEvents':int(self.r255.get('RISK_TRANCHE_FILL_EVENTS',0)),
            'riskTrancheFilledQty':float(self.r255.get('RISK_TRANCHE_FILL_QTY_MILLI',0))/1000.0,
            'riskTrancheAuthorizedRisk':float(self.riskTrancheAuthorizedRisk),
            'riskTrancheSpentRisk':float(self.riskTrancheSpentRisk),
            'riskTrancheReleasedRisk':float(self.riskTrancheReleasedRisk),
            'riskTrancheOverrunMax':float(self.riskTrancheOverrunMax),
            'riskDebtOutstanding':float(self._risk_debt_outstanding()),'riskDebtPeak':float(self.riskDebtPeak),
            'passiveRepairSubmitsAfterRiskTranche':int(self.passiveRepairSubmitsAfterRiskTranche),
            'passiveRepairFillsAfterRiskTranche':int(self.passiveRepairFillsAfterRiskTranche),
            'firstRepairAfterRiskTrancheMs':self.firstRepairAfterRiskTrancheMs,
            'sameGenerationRepairAfterRiskTranche':bool(self.sameGenerationRepairAfterRiskTranche),
            'riskTrancheLedger':list(self.riskTrancheMeta.values()),
            'r255CorrectnessPass':bool(correct)})
        return r

def slim(r):
    return {k:r.get(k) for k in ('pnlDiagnosticOnly','floor','best','fillEvents','filledQty','submits','unauthorizedOverflowQty','repairQuotaExcessMax')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r255_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort=json.loads(z.read('cohort.json'))['rows']; co={int(x['marketId']):x for x in cohort}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            sim=PreRepairRiskTrancheSim(tape,1,4)
            try:c=sim.run_r255(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R247_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R255_PRE_REPAIR_RISK_TRANCHE','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'riskSubmits':c['riskTrancheSubmits'],'riskFills':c['riskTrancheFillEvents'],
               'riskQty':c['riskTrancheFilledQty'],'riskDebtPeak':c['riskDebtPeak'],
               'passiveRepairSubmitsAfterRisk':c['passiveRepairSubmitsAfterRiskTranche'],
               'passiveRepairFillsAfterRisk':c['passiveRepairFillsAfterRiskTranche'],
               'firstRepairAfterRiskMs':c['firstRepairAfterRiskTrancheMs'],
               'sameGenerationRepairAfterRisk':c['sameGenerationRepairAfterRiskTranche'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_55_PRE_REPAIR_RISK_TRANCHE_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),
                      'riskTrancheExercised':any(x['riskFills']>0 for x in cmp),
                      'riskThenPassiveRepairExercised':any(x['riskFills']>0 and x['passiveRepairFillsAfterRisk']>0 for x in cmp)},
             'boundary':['R2.47 exact control/base','one pre-Repair same-direction venue-min risk tranche per responsibility generation','no pair/Floor/full-coverage wait','explicit risk debt is separate from Repair/continuation credit','passive Repair remains live','no Target/winner/future runtime input','<=180s unchanged','max_slots=4','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
