from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_base_slack_v1',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
v16=sibling('phase_admission_for_base_slack_v1',Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py'))
EPS=1e-9;v38=g.v38;v80=g.v80

class BaseRepairPriceSlackCandidate(front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,base_pair_slack=0.0,parent_no_chase_insurance=False,**kw):
        self.basePairSlack=float(base_pair_slack);self.baseSlackEvents=[]
        self.parentNoChaseInsurance=bool(parent_no_chase_insurance);self.parentRepairReferencePairSum={};self.parentInsuranceSubmitted=set();self.parentNoChaseBlocks=0;self.parentInsuranceAllows=0
        super().__init__(*a,**kw)

    def _submit_authorized(self,t,qv,z,roles_this_tick):
        if z is None:return False
        side,qty,_,role,oid=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9
        if qty<legal-EPS:
            return v16.v15.v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick)
        q=min(max(qty,legal),12.);x,v=self._feature(t,side,q,p)
        self.econFeatureRows.append({'t':int(t),'role':role,'side':side,'pairEdge':v['pair_edge'],'pairSum':v['candidate_pair_sum'],'floorRatio':v['floor_ratio'],'netReserveRatio':v['net_pair_reserve_ratio']})
        if role=='REPAIR':
            phase='baseAcquisitionRepair' if v['net_pair_reserve_ratio']<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));slack=self.basePairSlack if phase=='baseAcquisitionRepair' else 0.0;limit=env+slack;margin=limit-float(v['candidate_pair_sum'])
            ev={'t':int(t),'role':'REPAIR','phase':phase,'side':side,'qty':float(q),'price':float(p),'pairSum':float(v['candidate_pair_sum']),'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'floorRatio':float(v['floor_ratio']),'netReserveRatio':float(v['net_pair_reserve_ratio']),'projectedFloorDeltaRatio':float(v['projected_floor_delta_ratio']),'postPairCoverage':float(v['post_pair_coverage']),'postAbsnetRatio':float(v['post_absnet_ratio']),'secondsLeft':float(v['seconds_left']),'frozenEnvelope':env,'slack':slack,'effectiveLimit':limit,'margin':margin,'submit':False}
            if phase=='baseAcquisitionRepair':
                rsc,rth=self.economic.score('expensive_repair_recovers_30s',x);ev['baseRecoveryScore']=float(rsc);ev['baseRecoveryThreshold']=float(rth);ev['baseRecoveryMargin']=float(rsc-rth)
            if phase=='baseAcquisitionRepair':
                self.basePriceEval+=1;self.baseMargins.append(margin)
                pid=int(self.repairParent.get('id')) if getattr(self,'repairParent',None) is not None else -1
                ref=self.parentRepairReferencePairSum.setdefault(pid,float(v['candidate_pair_sum']))
                ev['parentId']=pid;ev['parentReferencePairSum']=float(ref);ev['parentNoChaseInsurance']=bool(self.parentNoChaseInsurance)
                if v['candidate_pair_sum']>limit+EPS:
                    self.basePriceBlock+=1;self._block_parent();ev['reason']='BASE_PRICE_ABOVE_SLACKED_ENVELOPE';self.baseSlackEvents.append(ev);return False
                above_frozen=bool(v['candidate_pair_sum']>env+EPS)
                if above_frozen and self.parentNoChaseInsurance:
                    if float(v['candidate_pair_sum'])>float(ref)+EPS:
                        self.basePriceBlock+=1;self.parentNoChaseBlocks+=1;self._block_parent();ev['reason']='PARENT_LOCAL_NO_CHASE_BLOCK';self.baseSlackEvents.append(ev);return False
                    if pid in self.parentInsuranceSubmitted:
                        self.basePriceBlock+=1;self.parentNoChaseBlocks+=1;self._block_parent();ev['reason']='PARENT_INSURANCE_ALREADY_USED_BLOCK';self.baseSlackEvents.append(ev);return False
                    ev['reason']='PARENT_LOCAL_IMPROVING_PRICE_INSURANCE_ALLOW';self.parentInsuranceAllows+=1
                else:
                    ev['reason']='BASE_PRICE_ACCEPT_SLACKED_ENVELOPE'
                self.basePriceAccept+=1
            else:
                self.reservePriceEval+=1;self.reserveMargins.append(env-float(v['candidate_pair_sum']))
                if v['candidate_pair_sum']>env+EPS:
                    self.reservePriceBlock+=1;self._block_parent();ev['reason']='RESERVE_PRICE_BLOCK_FROZEN';self.baseSlackEvents.append(ev);return False
                self.reservePriceAccept+=1
                if v['match_fraction']>0 and v['pair_edge']>=-EPS:self.reserveCheapAccept+=1
                else:
                    sc,th=self.economic.score('expensive_repair_recovers_30s',x);self.reserveExpRecoveryEval+=1;self.recoveryScores.append(sc)
                    if sc<th:
                        self.reserveExpRecoveryBlock+=1;self._block_parent();ev.update({'reason':'RESERVE_RECOVERY_BLOCK_FROZEN','recoveryScore':sc,'recoveryThreshold':th});self.baseSlackEvents.append(ev);return False
                    self.reserveExpRecoveryAccept+=1
                ev['reason']='RESERVE_REPAIR_ACCEPT_FROZEN'
            ok=v16.v15.v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick);ev['submit']=bool(ok)
            if ok and phase=='baseAcquisitionRepair' and self.parentNoChaseInsurance and ev.get('reason')=='PARENT_LOCAL_IMPROVING_PRICE_INSURANCE_ALLOW':
                self.parentInsuranceSubmitted.add(int(ev.get('parentId') or -1))
            self.baseSlackEvents.append(ev);return bool(ok)
        elif role=='EXPAND':
            u,d,cu,cd,cost,reserve,debt,un,hist=self._reconstruct_economics();floor=min(u,d)-cost
            if floor<-EPS:self.expandNegativeFloorBlock+=1;return False
            sc,th=self.economic.score('safe_expand_preserves_floor_30s',x);self.expandValueEval+=1;self.expandScores.append(sc)
            if sc<th:self.expandValueBlock+=1;return False
            self.expandValueAccept+=1
        return v16.v15.v14.StrictLaneSourceSim._submit_authorized(self,t,qv,z,roles_this_tick)

    def run_slack(self,models,winner):
        r=self.run_transition(models,winner)
        r.update({'baseRepairPairSlack':self.basePairSlack,'baseSlackEvents':self.baseSlackEvents[:240]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--base-pair-slack',type=float,required=True);ap.add_argument('--parent-no-chase-insurance',action='store_true');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if a.base_pair_slack<0 or a.base_pair_slack>0.5:raise ValueError('slack out of range')
    tmp=Path(tempfile.mkdtemp(prefix='eth_base_slack_v1_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'ETH_BASE_REPAIR_SLACK_V1','slack':a.base_pair_slack,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ETH_BASE_REPAIR_SLACK_V1_START','markets':mids,'slack':a.base_pair_slack}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for i,mid in enumerate(mids,1):
            cr=by[mid];sim=BaseRepairPriceSlackCandidate(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile(),base_pair_slack=a.base_pair_slack,parent_no_chase_insurance=a.parent_no_chase_insurance)
            try:
                r=sim.run_slack(models,cr['winner']);saf=front.safety(r);events=list(sim.baseSlackEvents);repair_orders=[o for o in sim.orders.values() if str(o.get('objective_role') or '')=='REPAIR']
                first_accept=next((e for e in events if e.get('submit')),None)
                row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'fills':int(r.get('actualFillEvents') or 0),'submits':int(r.get('submits') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentActiveAtEnd':int(r.get('repairParentActiveAtEnd') or (sim.repairParent is not None)),'repairOrderCount':len(repair_orders),'timingRepairSubmits':int(getattr(sim,'timingRepairSubmits',0)),'basePriceEval':int(getattr(sim,'basePriceEval',0)),'basePriceAccept':int(getattr(sim,'basePriceAccept',0)),'basePriceBlock':int(getattr(sim,'basePriceBlock',0)),'parentNoChaseBlocks':int(getattr(sim,'parentNoChaseBlocks',0)),'parentInsuranceAllows':int(getattr(sim,'parentInsuranceAllows',0)),'firstAcceptedRepair':first_accept,'baseSlackEvents':events[:320],'repairOrders':[{'side':o.get('side'),'price':o.get('price'),'qty':o.get('qty'),'placed':o.get('placed'),'status':o.get('status'),'cum':o.get('cum'),'objectiveId':o.get('objective_id'),'executionRole':o.get('execution_role')} for o in repair_orders],'transitionBlocks':int(r.get('transitionBlocks') or 0),'safety':saf}
            finally:sim.close()
            rows.append(row);print(json.dumps({'idx':i,'of':len(mids),'slack':a.base_pair_slack,**{k:row[k] for k in ['marketId','pnl','floor','fills','repairOrderCount','basePriceAccept','basePriceBlock']}},ensure_ascii=False),flush=True)
        pnls=[r['pnl'] for r in rows];floors=[r['floor'] for r in rows];traded=[r for r in rows if r['fills']>0];wins=[r for r in rows if r['pnl']>0];safety_zero=all(all(abs(float(v))<=EPS for v in r['safety'].values()) for r in rows)
        out={'version':'ETH_BASE_REPAIR_PRICE_ENVELOPE_SLACK_TUNING_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'basePairSlack':a.base_pair_slack,'parentNoChaseInsurance':bool(a.parent_no_chase_insurance),'markets':mids,'aggregate':{'marketCount':len(rows),'tradedMarkets':len(traded),'tradeCoverage':len(traded)/len(rows) if rows else 0.0,'positiveMarkets':len(wins),'winRateAllMarkets':len(wins)/len(rows) if rows else 0.0,'aggregatePnl':sum(pnls),'worstMarketPnl':min(pnls) if pnls else None,'worstTerminalFloor':min(floors) if floors else None,'repairOrderCount':sum(r['repairOrderCount'] for r in rows),'timingRepairSubmits':sum(r['timingRepairSubmits'] for r in rows),'basePriceEval':sum(r['basePriceEval'] for r in rows),'basePriceAccept':sum(r['basePriceAccept'] for r in rows),'basePriceBlock':sum(r['basePriceBlock'] for r in rows),'parentNoChaseBlocks':sum(r.get('parentNoChaseBlocks',0) for r in rows),'parentInsuranceAllows':sum(r.get('parentInsuranceAllows',0) for r in rows),'allSafetyZero':safety_zero},'rows':rows,'boundary':['only baseAcquisitionRepair additive pair-sum slack differs','ResponsibilityTransition Repair-first retained','AllocationLedger V2/RepairExecutionRouter frozen','timing/qty/price route frozen','reserveRepair and Expand economics frozen','<=180s no-new-exposure retained','no Target/winner/PnL runtime input','realistic HFT only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'slack':a.base_pair_slack,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
