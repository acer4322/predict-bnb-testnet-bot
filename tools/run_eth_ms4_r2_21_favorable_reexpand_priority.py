from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
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

class FavorableReexpandPrioritySim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.r221=Counter();self.repairLots=[];self.priorityKeys=set();self.priorityMeta={};self.r221events=[]
    def _clean_lots(self):
        g=int(self.scopeGeneration);self.repairLots=[x for x in self.repairLots if int(x['generation'])==g and float(x['remaining'])>EPS]
    def _eligible_lot_qty(self,side,pE):
        self._clean_lots();return sum(float(x['remaining']) for x in self.repairLots if x['side']==side and float(x['repairPrice'])+float(pE)<=1.0+EPS)
    def _consume_lots(self,side,pE,q):
        rem=float(q);used=[]
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if rem<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(pE)>1.0+EPS or float(x['remaining'])<=EPS:continue
            take=min(rem,float(x['remaining']));x['remaining']-=take;rem-=take;used.append({'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+float(pE)})
        return used,rem
    def _has_live_priority(self):
        for k in list(self.priorityKeys):
            o=self.orders.get(k)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES and int(self.key_scope_gen.get(k,-1))==int(self.scopeGeneration):return True
        return False
    def process(self,t):
        before_split=len(self.splitEvents);before_cum={k:float(self.orders.get(k,{}).get('cum') or 0.0) for k in self.priorityKeys}
        super().process(t)
        new=self.splitEvents[before_split:]
        for e in new:
            if e.get('event')!='ROLE_FILL_SPLIT':continue
            gen=int(e.get('generationAtSubmit') or -1);role=e.get('role');side=e.get('side');rq=float(e.get('repairAllocated') or 0.0);price=float(e.get('price') or 0.0);key=e.get('key')
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and rq>EPS and gen==int(self.scopeGeneration):
                surplus='DOWN' if side=='UP' else 'UP';self.repairLots.append({'t':int(e.get('t',t)),'generation':gen,'side':surplus,'repairPrice':price,'remaining':rq,'sourceKey':key,'sourceRole':role});self.r221['CONFIRMED_REPAIR_LOT_QTY_MILLI']+=int(round(rq*1000))
                self.r221events.append({'t':int(e.get('t',t)),'event':'CONFIRMED_REPAIR_SCHEDULING_LOT','generation':gen,'surplusSide':surplus,'repairPrice':price,'qty':rq,'sourceKey':key,'sourceRole':role})
            if key in self.priorityKeys:
                old=float(before_cum.get(key,0.0));cur=float(self.orders.get(key,{}).get('cum') or 0.0);inc=max(0.0,cur-old)
                if inc>EPS:
                    used,left=self._consume_lots(str(side),price,inc);self.r221['PRIORITY_REEXPAND_FILL']+=1;self.r221['PRIORITY_REEXPAND_FILL_QTY_MILLI']+=int(round(inc*1000));ev={'t':int(e.get('t',t)),'event':'FAVORABLE_REEXPAND_PRIORITY_FILL','key':key,'generation':gen,'side':side,'expandPrice':price,'fillQty':inc,'repairLotsConsumed':used,'unmatchedSchedulingQty':left};self.r221events.append(ev);self.slot_history.append(ev)
        self._clean_lots()
    def _try_priority_reexpand(self,t):
        if self.scopeSide is None or self._has_live_priority():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand
        elig=self._eligible_lot_qty(side,p)
        if elig+EPS<q:
            if elig>EPS:self.r221['PAIR_LOT_BELOW_VENUE_MIN_QTY']+=1
            return False
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)));credit=float(self._available_expand_risk_credit())
        if credit+EPS<risk:
            self.r221['EXISTING_MONETARY_CREDIT_STILL_BLOCKS']+=1;return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):return False
        key=f'{side}_{before_n}';self.priorityKeys.add(key);self.priorityMeta[key]={'t':int(t),'generation':int(self.scopeGeneration),'side':side,'price':float(p),'qty':float(q),'eligibleRepairQtyAtSubmit':elig};self.r221['PRIORITY_REEXPAND_SUBMIT']+=1
        ev={'t':int(t),'event':'FAVORABLE_REEXPAND_PRIORITY_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'expandPrice':float(p),'qty':float(q),'eligibleRepairQty':float(elig),'riskCost':risk,'availableCredit':credit};self.r221events.append(ev);self.slot_history.append(ev);return True
    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return super()._open_one_option(t,qv,end)
        if not self._has_stale_scope_reservation() and self._try_priority_reexpand(t):
            if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
            return
        return super()._open_one_option(t,qv,end)
    def run_r221(self,w):
        r=super().run_cap(w);r['r221Stats']=dict(self.r221);r['r221Events']=self.r221events[:2000];r['priorityReexpandSubmits']=int(self.r221.get('PRIORITY_REEXPAND_SUBMIT',0));r['priorityReexpandFills']=int(self.r221.get('PRIORITY_REEXPAND_FILL',0));r['remainingRepairSchedulingLots']=[x for x in self.repairLots if float(x['remaining'])>EPS][:100];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r221_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=FavorableReexpandPrioritySim(tape,4)
            try:c=sim.run_r221(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},{'marketId':mid,'cell':'MS4_R221_FAVORABLE_REEXPAND_PRIORITY','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},'candidate':{'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'prioritySub':c['priorityReexpandSubmits'],'priorityFill':c['priorityReexpandFills'],'stats':c['r221Stats']},'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R221_FAVORABLE_REEXPAND_PRIORITY'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'prioritySubmits':c['priorityReexpandSubmits'],'priorityFills':c['priorityReexpandFills']})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS for m in mids);anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_21_FAVORABLE_REEXPAND_PRIORITY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 is unchanged control','actual Repair allocation creates scheduling lots only; lots create no risk budget','priority re-expand requires enough unconsumed actual Repair quantity whose repairPrice+currentExpandPrice<=1 to fully cover venue-min Expand qty','priority re-expand must independently pass frozen existing realized monetary-credit risk budget','ordinary CAP1 actions are never pair-vetoed; pair economics only grants priority scheduling to an additional favorable-cycle opportunity','scheduling lots consume only on confirmed priority Expand fills','at most one priority re-expand live per current scope','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
