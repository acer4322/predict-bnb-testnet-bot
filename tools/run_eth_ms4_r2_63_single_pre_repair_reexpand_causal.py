from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
r255=r257.r255; v2=r257.v2; EPS=1e-9

class SinglePreRepairReexpandSim(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r263=Counter(); self.r263Events=[]; self.r263GenerationUsed=set(); self.r263Keys=set()

    def _live_dedicated_repair_rows(self,gen):
        out=[]
        for key in list(self.riskRepairCarrierKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(gen):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st in v2.TERMINAL_STATUSES:continue
            out.append((key,o))
        return out

    def _samegen_expand_live(self,gen,side):
        for _,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND'):
            if key in self.r263Keys:continue
            if int(self.key_scope_gen.get(key,-1))==int(gen) and str(o['side'])==str(side):return True
        return False

    def _try_r263(self,t,end):
        if self.scopeSide is None or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return False
        ob=self._obligation_current()
        if not ob:return False
        gen=int(ob['generation'])
        if gen in self.r263GenerationUsed:return False
        # Explicitly before any actual Repair payment for this risk obligation.
        if float(ob.get('repaidQty') or 0.0)>EPS:return False
        repairs=self._live_dedicated_repair_rows(gen)
        if not repairs:return False
        side=str(ob.get('scopeSideAtBirth') or self.scopeSide)
        if side not in {'UP','DOWN'}:return False
        if self._samegen_expand_live(gen,side):
            self.r263['BLOCK_ORDINARY_EXPAND_ALREADY_LIVE']+=1;return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self.r263['BLOCK_SHARED_CAPACITY']+=1;return False
        if self._has_stale_scope_reservation():
            self.r263['BLOCK_STALE_SCOPE']+=1;return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:
            self.r263['BLOCK_NO_EXPAND_CANDIDATE']+=1;return False
        p,q,proj,split=cand
        before_floor=float(self._physical_floor());after_floor=float(self._candidate_alone_floor(side,p,q))
        risk=max(0.0,before_floor-after_floor)
        if risk<=EPS:
            self.r263['BLOCK_NOT_RISK_BEARING']+=1;return False
        # Diagnostic only: live Repair is NOT credited as protection.
        repair_prices=[float(o['price']) for _,o in repairs]
        best_repair=min(repair_prices) if repair_prices else None
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self.r263['SUBMIT_BLOCKED']+=1;return False
        key=f'{side}_{before_n}'
        # Register as a second explicit risk-authority tranche. R2.55/R2.57 will
        # account confirmed fill and add its qty to the same Repair obligation.
        self.r263GenerationUsed.add(gen);self.r263Keys.add(key);self.riskTrancheKeys.add(key)
        meta={'key':key,'generation':gen,'scopeSide':side,'submittedAt':int(t),'price':float(p),'qty':float(q),
              'authorized':float(risk),'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
              'creditAvailableAtSubmit':float(self._available_expand_risk_credit()),
              'repairProgressAtSubmit':int(self.scopeRepairProgressClocks),'kind':'R263_PRE_REPAIR_REEXPAND'}
        self.riskTrancheMeta[key]=meta;self.riskTrancheAuthorizedRisk+=risk
        self.r263['SUBMIT']+=1
        ev={'t':int(t),'event':'R263_SINGLE_PRE_REPAIR_REEXPAND_SUBMIT','generation':gen,'key':key,'side':side,
            'price':float(p),'qty':float(q),'riskAuthorized':float(risk),'obligationOutstandingBefore':float(ob['outstanding']),
            'obligationRepaidBefore':float(ob.get('repaidQty') or 0.0),'liveRepairPrices':repair_prices,
            'diagnosticBestPairSum':(float(p)+best_repair if best_repair is not None else None),
            'physicalOccupancyAfter':len(self.slot_key)+len(self.activeKeys)}
        self.r263Events.append(ev);self.slot_history.append(ev);self._audit_r247();return True

    def _open_one_option(self,t,qv,end):
        # Keep exact R2.57 lifecycle first. R263 only consumes a spare option after
        # the actual risk obligation + live Repair carrier have materialized.
        super()._open_one_option(t,qv,end)
        self._try_r263(t,end)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key in self.r263Keys and inc>EPS:
                gen=int(ev.get('generationAtSubmit') or -1);price=float(ev.get('price') or 0.0)
                ob=self.riskRepairObligations.get(gen)
                self.r263['FILL']+=1;self.r263['FILL_QTY_MILLI']+=int(round(inc*1000))
                e={'t':int(t),'event':'R263_SINGLE_PRE_REPAIR_REEXPAND_FILL','generation':gen,'key':key,
                   'fillQty':inc,'price':price,'obligationOutstandingAfterBaseProcess':float(ob.get('outstanding') if ob else 0.0),
                   'obligationBornQtyAfter':float(ob.get('bornQty') if ob else 0.0)}
                self.r263Events.append(e);self.slot_history.append(e)

    def run_r263(self,winner):
        r=super().run_r257(winner)
        r.update({'r263Version':'MS4_R2_63_SINGLE_PRE_REPAIR_REEXPAND_CAUSAL_V1','r263Stats':dict(self.r263),
                  'r263Events':self.r263Events[:3000],'r263Submits':int(self.r263.get('SUBMIT',0)),
                  'r263Fills':int(self.r263.get('FILL',0)),'r263FilledQty':float(self.r263.get('FILL_QTY_MILLI',0))/1000.0,
                  'r263CorrectnessPass':bool(r.get('r255CorrectnessPass'))})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r263_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=SinglePreRepairReexpandSim(tape,1,4)
            try:c=sim.run_r263(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R263_SINGLE_PRE_REPAIR_REEXPAND','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'r263Submits':c['r263Submits'],'r263Fills':c['r263Fills'],'r263Qty':c['r263FilledQty'],
               'riskFills':c['riskTrancheFillEvents'],'obligationsBorn':c['riskRepairObligationsBorn'],
               'obligationsRepaid':c['riskRepairObligationsRepaid'],'passiveRepaidQty':c['riskRepairPassiveRepaidQty'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
               'correct':bool(c['r263CorrectnessPass']),'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0)),
               'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_63_SINGLE_PRE_REPAIR_REEXPAND_CAUSAL_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'r263SubmitExercised':any(x['r263Submits']>0 for x in cmp),
                      'r263FillExercised':any(x['r263Fills']>0 for x in cmp),
                      'noEffectControl1946784':all(abs(x[k])<=1e-9 for x in cmp if x['marketId']==1946784 for k in ['pnlDelta','floorDelta','bestDelta','gapDelta'])},
             'boundary':['exact R2.57 control','one R263 re-expand at most per generation','requires confirmed risk obligation + zero Repair payment + live dedicated passive Repair carrier','live Repair is not credit/protection','no pair/Floor/classifier gate','new confirmed fill enlarges explicit same-generation Repair obligation','V42 continuous repeat forbidden','max4','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed causal evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
