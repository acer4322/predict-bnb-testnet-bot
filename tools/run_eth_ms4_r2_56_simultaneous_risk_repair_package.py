from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_55_pre_repair_risk_tranche as r255
r247=r255.r247; v2=r255.v2; EPS=1e-9

class SimultaneousRiskRepairPackageSim(r255.PreRepairRiskTrancheSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r256=Counter(); self.r256Events=[]; self.packageRepairKeys=set(); self.packageMeta={}

    def _try_simultaneous_package(self,t,end):
        if self.scopeSide is None or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return False
        gen=int(self.scopeGeneration)
        if gen in self.riskTrancheGenerationUsed or int(self.scopeRepairProgressClocks)!=0:return False
        if self._has_stale_scope_reservation():return False
        # Two new passive carriers must fit inside the shared physical max-4 envelope.
        if len(self.slot_key)+len(self.activeKeys)+2>self.max_slots:
            self.r256['PACKAGE_SHARED_CAPACITY_BLOCK']+=1; return False
        risk_side=str(self.scopeSide); repair_side=self._repair_side()
        risk_cand=self._candidate_from_levels_v8(risk_side,'SATELLITE_EXPAND',False)
        repair_cand=self._candidate_from_levels_v8(repair_side,'ECONOMIC_CORE',False)
        if risk_cand is None:
            self.r256['PACKAGE_NO_RISK_MAKER_CANDIDATE']+=1; return False
        if repair_cand is None:
            self.r256['PACKAGE_NO_LEGAL_REPAIR_CARRIER']+=1; return False
        rp,rq,rproj,rsplit=risk_cand
        pp,pq,pproj,psplit=repair_cand
        if psplit is None or float(psplit.get('repairQty') or 0.0)<=EPS:
            self.r256['PACKAGE_REPAIR_HAS_NO_DEBT_ALLOCATION']+=1; return False
        before_floor=float(self._physical_floor()); after_floor=float(self._candidate_alone_floor(risk_side,rp,rq))
        risk=max(0.0,before_floor-after_floor)
        if risk<=EPS:
            self.r256['PACKAGE_NOT_RISK_BEARING']+=1; return False
        # Materialize risk first, then opposite passive Repair in same callback.
        risk_before_n=self.n
        if not self._submit_role_v8(t,risk_side,'SATELLITE_EXPAND',rp,rq,rproj,None):
            self.r256['PACKAGE_RISK_SUBMIT_BLOCKED']+=1; return False
        risk_key=f'{risk_side}_{risk_before_n}'
        repair_before_n=self.n
        if not self._submit_role_v8(t,repair_side,'ECONOMIC_CORE',pp,pq,pproj,psplit):
            # Do not silently leave an unintended naked package. Request immediate cancel;
            # native HFT lifecycle still decides whether cancel beats any late fill.
            for sid,key in list(self.slot_key.items()):
                if key==risk_key:self._request_cancel(t,int(sid),'R256_REPAIR_SUBMIT_FAILED');break
            self.r256['PACKAGE_REPAIR_SUBMIT_FAILED_AFTER_RISK']+=1
            return False
        repair_key=f'{repair_side}_{repair_before_n}'
        self.riskTrancheKeys.add(risk_key); self.riskTrancheGenerationUsed.add(gen)
        self.riskTrancheMeta[risk_key]={'key':risk_key,'generation':gen,'scopeSide':self.scopeSide,
            'submittedAt':int(t),'price':float(rp),'qty':float(rq),'authorized':float(risk),
            'held':float(risk),'spent':0.0,'released':0.0,'terminal':None,
            'creditAvailableAtSubmit':float(r247.BoundedCoreServiceFavorableRecycleSim._available_expand_risk_credit(self)),
            'repairProgressAtSubmit':int(self.scopeRepairProgressClocks)}
        self.riskTrancheAuthorizedRisk+=risk; self.r255['RISK_TRANCHE_SUBMIT']+=1
        self.packageRepairKeys.add(repair_key)
        self.packageMeta[gen]={'generation':gen,'submittedAt':int(t),'riskKey':risk_key,'repairKey':repair_key,
            'riskSide':risk_side,'riskPrice':float(rp),'riskQty':float(rq),'riskAuthorized':float(risk),
            'repairSide':repair_side,'repairPrice':float(pp),'repairQty':float(pq),
            'repairDebtQty':float(psplit['repairQty']),'repairOverflowQty':float(psplit['overflowQty']),
            'riskFillAt':None,'repairFillAt':None}
        self.r256['PACKAGE_SUBMIT']+=1; self.r256['PACKAGE_REPAIR_SUBMIT']+=1
        ev={'t':int(t),'event':'R256_SIMULTANEOUS_RISK_REPAIR_PACKAGE_SUBMIT',**self.packageMeta[gen],
            'physicalFloorBefore':before_floor,'riskCandidateFloor':after_floor,
            'riskDebtBefore':float(self._risk_debt_outstanding()),'liveSlotsAfter':len(self.slot_key)}
        self.r256Events.append(ev); self.slot_history.append(ev)
        self._audit_r247(); return True

    def _open_one_option(self,t,qv,end):
        # Run frozen R2.47 activity first, but skip naked R2.55 tranche.
        r247.BoundedCoreServiceFavorableRecycleSim._open_one_option(self,t,qv,end)
        self._try_simultaneous_package(t,end)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key')); gen=int(ev.get('generationAtSubmit') or -1); inc=float(ev.get('fillInc') or 0.0)
            if inc<=EPS or gen not in self.packageMeta:continue
            m=self.packageMeta[gen]
            if key==m['riskKey'] and m['riskFillAt'] is None:
                m['riskFillAt']=int(t); self.r256['PACKAGE_RISK_FILL']+=1
                self.r256Events.append({'t':int(t),'event':'R256_PACKAGE_RISK_FILL','generation':gen,'key':key,'qty':inc})
            if key==m['repairKey'] and m['repairFillAt'] is None:
                m['repairFillAt']=int(t); self.r256['PACKAGE_REPAIR_FILL']+=1
                self.r256Events.append({'t':int(t),'event':'R256_PACKAGE_REPAIR_FILL','generation':gen,'key':key,
                    'qty':inc,'repairAllocated':float(ev.get('repairAllocated') or 0.0)})
        for gen,m in self.packageMeta.items():
            if m.get('fillOrderClass'):continue
            a=m.get('riskFillAt'); b=m.get('repairFillAt')
            if a is not None and b is not None:m['fillOrderClass']='SAME_CLOCK' if a==b else ('RISK_FIRST' if a<b else 'REPAIR_FIRST')
            elif a is not None:m['fillOrderClass']='RISK_ONLY_SO_FAR'
            elif b is not None:m['fillOrderClass']='REPAIR_ONLY_SO_FAR'

    def run_r256(self,winner):
        r=super().run_r255(winner)
        both=sum(1 for x in self.packageMeta.values() if x.get('riskFillAt') is not None and x.get('repairFillAt') is not None)
        risk_only=sum(1 for x in self.packageMeta.values() if x.get('riskFillAt') is not None and x.get('repairFillAt') is None)
        repair_only=sum(1 for x in self.packageMeta.values() if x.get('riskFillAt') is None and x.get('repairFillAt') is not None)
        r.update({'r256Version':'MS4_R2_56_SIMULTANEOUS_RISK_REPAIR_PACKAGE_V1','r256Stats':dict(self.r256),
            'r256Events':self.r256Events[:3000],'riskRepairPackages':list(self.packageMeta.values()),
            'riskRepairPackageSubmits':int(self.r256.get('PACKAGE_SUBMIT',0)),
            'riskRepairPackageBothFill':both,'riskRepairPackageRiskOnlyFill':risk_only,'riskRepairPackageRepairOnlyFill':repair_only})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r256_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[];cmp=[]
        for mid in mids:
            w=co[mid]['winner'];tape=tmp/f'{mid}.json.xz'
            bsim=r247.BoundedCoreServiceFavorableRecycleSim(tape,1,4)
            try:b=bsim.run_r247(w)
            finally:bsim.close()
            sim=SimultaneousRiskRepairPackageSim(tape,1,4)
            try:c=sim.run_r256(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R247_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R256_SIMULTANEOUS_RISK_REPAIR','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'packages':c['riskRepairPackageSubmits'],'bothFill':c['riskRepairPackageBothFill'],
               'riskOnlyFill':c['riskRepairPackageRiskOnlyFill'],'repairOnlyFill':c['riskRepairPackageRepairOnlyFill'],
               'riskFills':c['riskTrancheFillEvents'],'passiveRepairFillsAfterRisk':c['passiveRepairFillsAfterRiskTranche'],
               'firstRepairAfterRiskMs':c['firstRepairAfterRiskTrancheMs'],'riskDebtPeak':c['riskDebtPeak'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'correct':bool(c['r255CorrectnessPass'])}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_56_SIMULTANEOUS_RISK_REPAIR_PACKAGE_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'packageExercised':any(x['packages']>0 for x in cmp),
                      'bothLegsFillExercised':any(x['bothFill']>0 for x in cmp),
                      'riskThenPassiveRepairExercised':any(x['riskFills']>0 and x['passiveRepairFillsAfterRisk']>0 for x in cmp)},
             'boundary':['R2.47 exact control/base','risk and legal opposite passive Core Repair materialize in same callback','no prior Repair/pair/Floor/full-cover wait','one package per pre-Repair scope generation','explicit risk debt','max4 shared physical carrier admission','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781','consumed mechanism evidence only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
