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
v2=r28.v2;EPS=1e-9

class ReplenishableFavorablePairCapacitySim(r28.FanoutRoleCapacitySim):
    """R2.23: one-use actual Repair lots may replenish favorable Expand capacity.

    Ordinary CAP1 behavior remains first priority. Only when the ordinary monetary-credit
    budget is insufficient may an otherwise-idle receipt use actual, unconsumed Repair
    allocations whose repairPrice + current Expand price <= 1 to authorize one venue-min
    SATELLITE_EXPAND. Replenishment keys are excluded from aggregate monetary-credit
    reservation/consumption; their authority is the one-use Repair-lot reservation itself.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots)
        self.r223=Counter();self.repairLots=[];self.nextLotId=1
        self.replenishmentKeys=set();self.keyLotReservations={};self.r223Events=[]

    def _clean_lots(self):
        g=int(self.scopeGeneration)
        self.repairLots=[x for x in self.repairLots if int(x['generation'])==g and float(x['remaining'])>EPS]

    def _pending_ordinary_claims(self):
        # Existing live ordinary Expand has first claim on favorable Repair lots because
        # an actual ordinary fill must consume such lots before any later replenishment.
        self._clean_lots();free={int(x['lotId']):float(x['remaining']) for x in self.repairLots};claims=Counter()
        for _,key,o,role in self._live_role_rows(role='SATELLITE_EXPAND'):
            if key in self.replenishmentKeys or int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            need=float(self._remaining(key));side=str(o['side']);pE=float(o['price'])
            if need<=EPS:continue
            for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
                if need<=EPS:break
                lid=int(x['lotId']);avail=max(0.0,float(free.get(lid,0.0)))
                if avail<=EPS or x['side']!=side or float(x['repairPrice'])+pE>1.0+EPS:continue
                take=min(need,avail);free[lid]=avail-take;claims[lid]+=take;need-=take
        return claims

    def _gross_eligible_qty(self,side,pE):
        self._clean_lots();return float(sum(float(x['remaining']) for x in self.repairLots
            if x['side']==side and float(x['repairPrice'])+float(pE)<=1.0+EPS))

    def _eligible_qty(self,side,pE):
        self._clean_lots();claims=self._pending_ordinary_claims()
        return float(sum(max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0))) for x in self.repairLots
                         if x['side']==side and float(x['repairPrice'])+float(pE)<=1.0+EPS))

    def _reserve_lots(self,side,pE,q):
        self._clean_lots();claims=self._pending_ordinary_claims();need=float(q)
        elig=[x for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t'])))
              if x['side']==side and float(x['repairPrice'])+float(pE)<=1.0+EPS and float(x['remaining'])>EPS]
        if sum(max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0))) for x in elig)+EPS<need:return None
        out=[]
        for x in elig:
            if need<=EPS:break
            free=max(0.0,float(x['remaining'])-float(claims.get(int(x['lotId']),0.0)))
            if free<=EPS:continue
            take=min(need,free);x['remaining']-=take;need-=take
            out.append({'lot':x,'remaining':float(take),'authorized':float(take),'pairSum':float(x['repairPrice'])+float(pE)})
        return out if need<=EPS else None

    def _rollback_reservation(self,res):
        for a in res or []:
            q=float(a.get('remaining') or 0.0)
            if q>EPS:a['lot']['remaining']+=q;a['remaining']=0.0

    def _consume_reserved(self,key,fill_qty):
        rem=float(fill_qty);used=[]
        for a in self.keyLotReservations.get(key,[]):
            if rem<=EPS:break
            avail=float(a.get('remaining') or 0.0)
            if avail<=EPS:continue
            take=min(rem,avail);a['remaining']=avail-take;rem-=take
            used.append({'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),
                         'qty':float(take),'pairSum':float(a['pairSum'])})
        return used,rem

    def _consume_available_for_ordinary_expand(self,side,pE,q):
        self._clean_lots();rem=float(q);used=[]
        for x in sorted(self.repairLots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if rem<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(pE)>1.0+EPS or float(x['remaining'])<=EPS:continue
            take=min(rem,float(x['remaining']));x['remaining']-=take;rem-=take
            used.append({'lotId':int(x['lotId']),'repairPrice':float(x['repairPrice']),'qty':float(take),
                         'pairSum':float(x['repairPrice'])+float(pE)})
        if used:
            self.r223['ORDINARY_EXPAND_CONSUMED_REPAIR_LOT']+=1
            self.r223['ORDINARY_EXPAND_CONSUMED_QTY_MILLI']+=int(round(sum(x['qty'] for x in used)*1000))
        return used,rem

    def _reserved_current_expand_risk(self):
        total=float(super()._reserved_current_expand_risk())
        # Replenishment orders are physical exposure, but their authority is already reserved
        # by one-use Repair lots; do not double-reserve aggregate monetary credit.
        exempt=0.0
        for key in list(self.replenishmentKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st in v2.TERMINAL_STATUSES:continue
            rem=self._remaining(key)
            if rem>EPS:exempt+=float(rem)*float(o['price'])
        return float(max(0.0,total-exempt))

    def _release_terminal_replenishment(self,t):
        for key,res in list(self.keyLotReservations.items()):
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:continue
            released=0.0
            for a in res:
                q=float(a.get('remaining') or 0.0)
                if q>EPS:
                    a['lot']['remaining']+=q;a['remaining']=0.0;released+=q
            if released>EPS:
                self.r223['REPLENISHMENT_UNUSED_LOT_RELEASE_QTY_MILLI']+=int(round(released*1000))
                self.r223Events.append({'t':int(t),'event':'REPLENISHMENT_UNUSED_LOT_RELEASE','key':key,'qty':released,'status':st})
            self.keyLotReservations.pop(key,None)

    def _refresh_slots(self,t:int):
        super()._refresh_slots(t);self._release_terminal_replenishment(t)

    def process(self,t):
        before_split=len(self.splitEvents)
        super().process(t)
        new=self.splitEvents[before_split:]
        for e in new:
            if e.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(e.get('key'));gen=int(e.get('generationAtSubmit') or -1);role=e.get('role');side=str(e.get('side'))
            price=float(e.get('price') or 0.0);rq=float(e.get('repairAllocated') or 0.0);inc=float(e.get('fillInc') or 0.0)
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and rq>EPS and gen==int(self.scopeGeneration):
                surplus='DOWN' if side=='UP' else 'UP';lot={'lotId':self.nextLotId,'t':int(e.get('t',t)),'generation':gen,
                    'side':surplus,'repairPrice':price,'remaining':rq,'bornQty':rq,'sourceKey':key,'sourceRole':role}
                self.nextLotId+=1;self.repairLots.append(lot);self.r223['REPAIR_LOT_BORN']+=1;self.r223['REPAIR_LOT_QTY_MILLI']+=int(round(rq*1000))
            if role=='SATELLITE_EXPAND' and inc>EPS:
                if key in self.replenishmentKeys:
                    used,left=self._consume_reserved(key,inc)
                    pair_gain=sum((1.0-float(x['pairSum']))*float(x['qty']) for x in used)
                    self.r223['REPLENISHMENT_EXPAND_FILL']+=1;self.r223['REPLENISHMENT_EXPAND_FILL_QTY_MILLI']+=int(round(inc*1000))
                    self.r223['REPLENISHMENT_MATCHED_FLOOR_GAIN_MICRO']+=int(round(pair_gain*1_000_000))
                    # Frozen V8 treats every SATELLITE_EXPAND fill as monetary-credit spend.
                    # Reverse that bookkeeping only for this key; physical overflow accounting remains unchanged.
                    if gen==int(self.scopeGeneration):
                        self.scopeRiskCreditConsumed=max(0.0,float(self.scopeRiskCreditConsumed)-inc*price)
                    ev={'t':int(e.get('t',t)),'event':'REPLENISHMENT_EXPAND_FILL','key':key,'generation':gen,'side':side,
                        'expandPrice':price,'fillQty':inc,'repairLotsConsumed':used,'unmatchedAuthorizedQty':left,
                        'marginalFloorGain':pair_gain}
                    self.r223Events.append(ev);self.slot_history.append(ev)
                    if left>EPS:self.r223['REPLENISHMENT_FILL_EXCEEDED_RESERVED_LOT_QTY_MILLI']+=int(round(left*1000))
                else:
                    self._consume_available_for_ordinary_expand(side,price,inc)
        self._clean_lots()

    def _has_live_replenishment(self):
        for key in list(self.replenishmentKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(self.scopeGeneration):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False

    def _try_replenishment(self,t,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS or self.scopeSide is None:return False
        if self._has_stale_scope_reservation() or self._has_live_replenishment():return False
        side=str(self.scopeSide)
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand
        risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
        credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return False  # ordinary CAP1 monetary-credit path owns this opportunity
        gross=self._gross_eligible_qty(side,p);elig=self._eligible_qty(side,p)
        if elig+EPS<q:
            if gross+EPS>=q:self.r223['PENDING_ORDINARY_EXPAND_CLAIM_BLOCK']+=1
            elif elig>EPS:self.r223['PAIR_LOT_BELOW_VENUE_MIN_QTY']+=1
            return False
        res=self._reserve_lots(side,p,q)
        if not res:return False
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):
            self._rollback_reservation(res);return False
        key=f'{side}_{before_n}';self.replenishmentKeys.add(key);self.keyLotReservations[key]=res
        self.r223['REPLENISHMENT_EXPAND_SUBMIT']+=1
        ev={'t':int(t),'event':'REPLENISHMENT_FAVORABLE_PAIR_EXPAND_SUBMIT','key':key,'generation':int(self.scopeGeneration),
            'side':side,'expandPrice':float(p),'qty':float(q),'riskCost':risk,'ordinaryAvailableCredit':credit,
            'reservedRepairLots':[{'lotId':int(a['lot']['lotId']),'repairPrice':float(a['lot']['repairPrice']),
                                   'qty':float(a['authorized']),'pairSum':float(a['pairSum'])} for a in res]}
        self.r223Events.append(ev);self.slot_history.append(ev);return True

    def _open_one_option(self,t,qv,end):
        # Ordinary CAP1 gets first refusal on every receipt. Replenishment only uses a receipt
        # where CAP1 did not create a new option, so the pair rule never directly vetoes CAP1.
        before_last=self._last_new_receipt
        super()._open_one_option(t,qv,end)
        if self._last_new_receipt==int(t):return
        self._try_replenishment(t,end)

    def run_r223(self,w):
        r=super().run_cap(w);r['r223Stats']=dict(self.r223);r['r223Events']=self.r223Events[:2500]
        r['replenishmentSubmits']=int(self.r223.get('REPLENISHMENT_EXPAND_SUBMIT',0));r['replenishmentFills']=int(self.r223.get('REPLENISHMENT_EXPAND_FILL',0))
        r['replenishmentMatchedFloorGain']=float(self.r223.get('REPLENISHMENT_MATCHED_FLOOR_GAIN_MICRO',0))/1_000_000.0
        r['remainingRepairLots']=[{k:v for k,v in x.items() if k!='_'} for x in self.repairLots if float(x['remaining'])>EPS][:200]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r223_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=ReplenishableFavorablePairCapacitySim(tape,4)
            try:c=sim.run_r223(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},
                     {'marketId':mid,'cell':'MS4_R223_REPLENISHABLE_FAVORABLE_PAIR_CAPACITY','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                'candidate':{'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'replSub':c['replenishmentSubmits'],'replFill':c['replenishmentFills'],'replFloorGain':c['replenishmentMatchedFloorGain'],'stats':c['r223Stats']},
                'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R223_REPLENISHABLE_FAVORABLE_PAIR_CAPACITY'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,
                'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],
                'replenishmentSubmits':c['replenishmentSubmits'],'replenishmentFills':c['replenishmentFills'],'replenishmentMatchedFloorGain':c['replenishmentMatchedFloorGain']})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS and int(C[m].get('r223Stats',{}).get('REPLENISHMENT_FILL_EXCEEDED_RESERVED_LOT_QTY_MILLI',0))==0 for m in mids)
        anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        exercised=sum(C[m]['replenishmentFills'] for m in mids)>0
        out={'version':'MS4_R2_23_REPLENISHABLE_FAVORABLE_PAIR_CAPACITY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,
             'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti,'replenishmentLaneExercised':exercised},
             'boundary':['CAP1 ordinary action receives first refusal on every receipt','only actual current-generation Repair allocations mint one-use replenishment lots','ordinary Expand fills consume eligible favorable Repair lots first, preventing later reuse','replenishment is considered only when ordinary monetary credit is insufficient and full venue-min Expand qty is covered by unconsumed Repair lots with repairPrice+expandPrice<=1','replenishment keys are excluded from aggregate monetary-credit reservation and confirmed monetary-credit consumption; physical overflow accounting remains intact','at most one replenishment Expand live per scope','ordinary CAP1 actions are never pair-vetoed','<=180s unchanged','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
