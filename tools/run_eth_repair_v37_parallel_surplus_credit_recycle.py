from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,math,threading,time,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_v36_path=Path(__file__).with_name('run_eth_repair_v36_event_confirmed_shared_active_child.py')
_spec=importlib.util.spec_from_file_location('eth_v36_staged_for_v37',_v36_path)
if _spec is None or _spec.loader is None: raise ImportError(f'cannot load V36 from {_v36_path}')
v36=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=v36;_spec.loader.exec_module(v36)
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9
TERMINAL={'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

class V37ParallelSurplus(v36.V36EventConfirmedActive):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self._repairFillSeen={}
        self._creditNextId=1
        self.creditTranches=[]
        self.surplusOrders={}
        self.v37Events=[]
        self.creditMintedQty=0.0
        self.creditReservedQty=0.0
        self.creditConsumedQty=0.0
        self.creditReturnedQty=0.0
        self.creditExpiredQty=0.0
        self.creditOverspend=0.0
        self.crossParentCreditLeak=0
        self.surplusSubmitCount=0
        self.surplusFillQty=0.0
        self.surplusMatchedGain=0.0
        self.nonPositivePairFillQty=0.0
        self.lateSurplusFillAfterParentCompletion=0.0
        self.creditHeldBelowLegal=0
        self.creditHeldEconomicallyIneligible=0
        self.creditSignalHold=0
        self.parentAtLastProcess=None

    def _floor(self):
        try:return float(self._raw_floor()[0])
        except Exception:return 0.0

    def _carrier_fill_price(self,key,e):
        o=self.orders.get(key)
        if not o:return float(e.get('price') or 0.0)
        try:return float(v1.fill_price(o['side'],self.snap(o),float(o.get('price') or 0.0)))
        except Exception:return float(o.get('price') or e.get('price') or 0.0)

    def _scan_passive_repair_credit(self,t):
        current_pid=int(self.repairParent.get('id')) if self.repairParent is not None else None
        for key,e in list(getattr(self,'carrierLedger',{}).items()):
            if e.get('objectiveRole')!='REPAIR':continue
            lane=str(e.get('lane') or '')
            if lane.startswith('ACTIVE_'):continue
            pid=e.get('parentId')
            if pid is None:continue
            try:pid=int(pid)
            except Exception:continue
            now=float(e.get('actualFilled') or 0.0);old=float(self._repairFillSeen.get(key,0.0))
            if now<=old+EPS:
                self._repairFillSeen[key]=max(old,now);continue
            inc=now-old;self._repairFillSeen[key]=now
            if current_pid is None or pid!=current_pid:
                # Never mint stale/cross-parent credit.
                continue
            px=self._carrier_fill_price(key,e)
            if not (0.0<px<1.0):continue
            tr={'id':self._creditNextId,'parentId':pid,'repairKey':key,'repairPrice':float(px),'remaining':float(inc),'mintedQty':float(inc),'createdAt':int(t)}
            self._creditNextId+=1;self.creditTranches.append(tr);self.creditMintedQty+=inc
            self.v37Events.append({'event':'V37_PASSIVE_REPAIR_CREDIT_MINT','t':int(t),'parentId':pid,'repairKey':key,'repairPrice':float(px),'qty':float(inc),'floorAfterRepairObserved':self._floor()})

    def _tranche(self,tid):
        for z in self.creditTranches:
            if int(z['id'])==int(tid):return z
        return None

    def _order_status(self,key):
        o=self.orders.get(key)
        if not o:return None
        try:return self.snap(o).get('status')
        except Exception:return None

    def _reconcile_surplus_orders(self,t):
        current_pid=int(self.repairParent.get('id')) if self.repairParent is not None else None
        for key,a in list(self.surplusOrders.items()):
            e=self.carrierLedger.get(key,{})
            af=float(e.get('actualFilled') or 0.0);old=float(a.get('fillSeen') or 0.0)
            if af>old+EPS:
                inc=af-old;a['fillSeen']=af;self.surplusFillQty+=inc
                px=self._carrier_fill_price(key,e)
                left=inc
                for al in a['allocations']:
                    free=max(0.0,float(al['qty'])-float(al.get('consumed') or 0.0))
                    use=min(free,left)
                    if use<=EPS:continue
                    al['consumed']=float(al.get('consumed') or 0.0)+use;left-=use;self.creditConsumedQty+=use
                    tr=self._tranche(al['trancheId']);rp=float(tr['repairPrice']) if tr else float(al['repairPrice'])
                    pair=rp+float(px);gain=use*(1.0-pair);self.surplusMatchedGain+=gain
                    if pair>=1.0-EPS:self.nonPositivePairFillQty+=use
                    if current_pid is None or int(a['parentId'])!=int(current_pid):self.lateSurplusFillAfterParentCompletion+=use
                    self.v37Events.append({'event':'V37_SURPLUS_RECYCLE_FILL','t':int(t),'parentId':int(a['parentId']),'qty':float(use),'repairPrice':rp,'reexpandFillPrice':float(px),'pairSum':pair,'theoreticalMatchedFloorGain':gain,'parentStillCurrent':bool(current_pid is not None and int(a['parentId'])==int(current_pid))})
                    if left<=EPS:break
                if left>1e-7:self.creditOverspend+=left
            st=self._order_status(key)
            terminal=st is not None and str(st).upper() in TERMINAL
            stale=current_pid is None or int(a['parentId'])!=int(current_pid)
            if stale and not terminal and key not in self.cancelRequestedAt:
                if self._cancel_key(t,key):self.v37Events.append({'event':'V37_CANCEL_STALE_SURPLUS','t':int(t),'key':key,'oldParentId':int(a['parentId']),'currentParentId':current_pid})
            if terminal:
                # Return only unfilled reservation that still belongs to the same live parent; otherwise expire it.
                for al in a['allocations']:
                    unused=max(0.0,float(al['qty'])-float(al.get('consumed') or 0.0))
                    if unused<=EPS:continue
                    tr=self._tranche(al['trancheId'])
                    if tr is None:continue
                    if current_pid is not None and int(tr['parentId'])==int(current_pid):
                        tr['remaining']=float(tr.get('remaining') or 0.0)+unused;self.creditReturnedQty+=unused
                    else:self.creditExpiredQty+=unused
                a['terminal']=True;a['terminalStatus']=str(st).upper();a['terminalAt']=int(t)
        # Drop terminal orders from live set but keep object for audit in events only.
        for key in [k for k,a in self.surplusOrders.items() if a.get('terminal')]:self.surplusOrders.pop(key,None)

    def _expire_stale_credit(self,t):
        current_pid=int(self.repairParent.get('id')) if self.repairParent is not None else None
        for tr in self.creditTranches:
            rem=float(tr.get('remaining') or 0.0)
            if rem<=EPS:continue
            if current_pid is None or int(tr['parentId'])!=int(current_pid):
                self.creditExpiredQty+=rem;tr['remaining']=0.0
                self.v37Events.append({'event':'V37_CREDIT_EXPIRE_PARENT_CHANGE','t':int(t),'parentId':int(tr['parentId']),'qty':rem,'currentParentId':current_pid})

    def _live_surplus_exists(self):
        return bool(self.surplusOrders)

    def _maybe_surplus_recycle(self,t):
        if self._live_surplus_exists():return False
        if self.repairParent is None or self.thesis is None:return False
        if int(self.capEnd)-int(t)<=180000:return False
        pid=int(self.repairParent.get('id'));side=self.thesis.get('side')
        if side not in ('UP','DOWN'):return False
        if self._floor()>=-EPS:return False
        qv=v1.quotes(self.book)
        if not qv or side not in qv or qv[side].get('bid') is None:return False
        sig=self._signal_side(qv)
        if sig!=side:self.creditSignalHold+=1;return False
        eprice=float(qv[side]['bid'])
        if eprice<=EPS or eprice>=1.0-EPS:return False
        eligible=[];total=0.0
        for tr in self.creditTranches:
            if int(tr['parentId'])!=pid:continue
            rem=float(tr.get('remaining') or 0.0)
            if rem<=EPS:continue
            if float(tr['repairPrice'])+eprice<1.0-EPS:
                eligible.append(tr);total+=rem
        if total<=EPS:
            if any(int(z['parentId'])==pid and float(z.get('remaining') or 0.0)>EPS for z in self.creditTranches):self.creditHeldEconomicallyIneligible+=1
            return False
        legal=1.0/eprice
        q=min(total,12.0)
        if q+EPS<legal:
            self.creditHeldBelowLegal+=1;return False
        allocations=[];left=q
        for tr in eligible:
            take=min(float(tr['remaining']),left)
            if take<=EPS:continue
            tr['remaining']-=take;allocations.append({'trancheId':int(tr['id']),'qty':take,'consumed':0.0,'repairPrice':float(tr['repairPrice'])});left-=take
            if left<=EPS:break
        reserved=sum(float(x['qty']) for x in allocations)
        if reserved<=EPS:return False
        oid=self._new_objective('EXPAND',side)['id'];n0=self.n
        self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='SURPLUS_RECYCLE'
        ok=self.submit(t,side,eprice,reserved)
        if not ok:
            for al in allocations:
                tr=self._tranche(al['trancheId'])
                if tr:tr['remaining']+=float(al['qty'])
            return False
        key=f'{side}_{n0}'
        if key in self.carrierLedger:
            self.carrierLedger[key]['parentId']=pid;self.carrierLedger[key]['lane']='SURPLUS_RECYCLE';self.carrierLedger[key]['objectiveRole']='EXPAND';self.carrierLedger[key]['objectiveId']=oid
        self.surplusOrders[key]={'key':key,'parentId':pid,'side':side,'submitAt':int(t),'price':eprice,'qty':reserved,'allocations':allocations,'fillSeen':0.0}
        self.creditReservedQty+=reserved;self.surplusSubmitCount+=1
        self.v37Events.append({'event':'V37_SURPLUS_RECYCLE_SUBMIT','t':int(t),'parentId':pid,'side':side,'price':eprice,'qty':reserved,'legalMin':legal,'eligibleCredit':total,'allocations':[{'trancheId':x['trancheId'],'qty':x['qty'],'repairPrice':x['repairPrice'],'pairSumAtSubmit':x['repairPrice']+eprice} for x in allocations],'floorBeforeSubmit':self._floor()})
        return True

    def process(self,t):
        super().process(t)
        self._scan_passive_repair_credit(t)
        self._reconcile_surplus_orders(t)
        self._expire_stale_credit(t)
        self._maybe_surplus_recycle(t)
        self.parentAtLastProcess=int(self.repairParent.get('id')) if self.repairParent is not None else None

    def cancel_expired(self,t):
        super().cancel_expired(t)
        self._reconcile_surplus_orders(t)
        self._expire_stale_credit(t)
        self._maybe_surplus_recycle(t)

    def run_exam_v37(self,models,winner):
        r=super().run_exam_v36(models,winner)
        self._reconcile_surplus_orders(int(self.meta['lastReceivedMs']))
        remaining=sum(float(z.get('remaining') or 0.0) for z in self.creditTranches)
        reserved_live=sum(sum(max(0.0,float(a0['qty'])-float(a0.get('consumed') or 0.0)) for a0 in a['allocations']) for a in self.surplusOrders.values())
        overs=max(0.0,self.creditConsumedQty+self.creditExpiredQty+remaining+reserved_live-self.creditMintedQty)
        self.creditOverspend=max(self.creditOverspend,overs)
        r.update({
            'v37CreditMintedQty':self.creditMintedQty,'v37CreditReservedQty':self.creditReservedQty,'v37CreditConsumedQty':self.creditConsumedQty,
            'v37CreditReturnedQty':self.creditReturnedQty,'v37CreditExpiredQty':self.creditExpiredQty,'v37CreditRemainingQty':remaining,'v37CreditOverspend':self.creditOverspend,
            'v37CrossParentCreditLeak':self.crossParentCreditLeak,'v37SurplusSubmitCount':self.surplusSubmitCount,'v37SurplusFillQty':self.surplusFillQty,
            'v37SurplusMatchedGain':self.surplusMatchedGain,'v37NonPositivePairFillQty':self.nonPositivePairFillQty,
            'v37LateSurplusFillAfterParentCompletion':self.lateSurplusFillAfterParentCompletion,'v37CreditHeldBelowLegal':self.creditHeldBelowLegal,
            'v37CreditHeldEconomicallyIneligible':self.creditHeldEconomicallyIneligible,'v37CreditSignalHold':self.creditSignalHold,
            'v37Events':self.v37Events[:300]
        })
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v37_parallel_surplus_'))
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(10):print(json.dumps({'heartbeat':'V37_RUN','ts':time.time()}),flush=True)
    threading.Thread(target=heartbeat,daemon=True).start();print(json.dumps({'heartbeat':'V37_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort}
        models,life,cap,tim,econ,price,sur=v36.v34.v30.load_runtime(a);rows=[]
        for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=v36.V36EventConfirmedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try:br=b.run_exam_v36(models,cr['winner'])
            finally:b.close()
            s=V37ParallelSurplus(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try:ar=s.run_exam_v37(models,cr['winner'])
            finally:s.close()
            rows.append({'marketId':mid,'baseline':br,'candidate':ar})
            print(json.dumps({'marketId':mid,'creditMinted':ar['v37CreditMintedQty'],'surplusSubmit':ar['v37SurplusSubmitCount'],'surplusFillQty':ar['v37SurplusFillQty'],'matchedGain':ar['v37SurplusMatchedGain'],'baselineFloor':br['floor'],'candidateFloor':ar['floor'],'baselineAbsNet':br.get('absNet'),'candidateAbsNet':ar.get('absNet'),'nonPositivePairFillQty':ar['v37NonPositivePairFillQty'],'lateAfterParent':ar['v37LateSurplusFillAfterParentCompletion']},ensure_ascii=False),flush=True)
        def sm(side,key):return sum(float(x[side].get(key) or 0.0) for x in rows)
        agg={
            'markets':len(rows),'creditMintedQty':sm('candidate','v37CreditMintedQty'),'creditConsumedQty':sm('candidate','v37CreditConsumedQty'),
            'surplusSubmits':int(sm('candidate','v37SurplusSubmitCount')),'surplusFillQty':sm('candidate','v37SurplusFillQty'),'surplusMatchedGain':sm('candidate','v37SurplusMatchedGain'),
            'nonPositivePairFillQty':sm('candidate','v37NonPositivePairFillQty'),'creditOverspend':sm('candidate','v37CreditOverspend'),
            'crossParentCreditLeak':int(sm('candidate','v37CrossParentCreditLeak')),'lateSurplusFillAfterParentCompletion':sm('candidate','v37LateSurplusFillAfterParentCompletion'),
            'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),
            'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),'baselineAbsNetSum':sm('baseline','absNet'),'candidateAbsNetSum':sm('candidate','absNet'),
            'baselineThesisFirstFills':sm('baseline','thesisFirstActualFills'),'candidateThesisFirstFills':sm('candidate','thesisFirstActualFills')
        }
        gates={
            'cycleExercised':agg['surplusFillQty']>EPS,
            'positiveMatchedEconomics':agg['nonPositivePairFillQty']<=EPS and agg['surplusMatchedGain']>EPS,
            'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,
            'zeroCreditOverspend':agg['creditOverspend']<=1e-7,'zeroCrossParentCreditLeak':agg['crossParentCreditLeak']==0,
            'zeroLateFillAfterParentCompletion':agg['lateSurplusFillAfterParentCompletion']<=EPS,
            'floorNotWorse':agg['candidateFloorSum']>=agg['baselineFloorSum']-1e-7,
            'activityNotCollapsed':agg['candidateThesisFirstFills']>=0.8*agg['baselineThesisFirstFills']
        }
        out={'version':'ETH_REPAIR_V37_PARALLEL_SURPLUS_REPAIR_CREDIT_RECYCLE','researchOnly':True,'behaviorChange':True,'actionAuthorityScope':'realistic-HFT research only; no 8781','aggregate':agg,'gates':gates,'rows':rows,'boundary':['actual passive Maker Repair fills mint same-share credit','V36 active/Taker Repair does not mint V37 credit','same-thesis passive re-expand only','repairPrice + reexpandPrice < 1 natural economic boundary','re-expand qty <= realized eligible credit and venue legal minimum; no rounding up','Repair parent stays authoritative and is not replaced','<=180s no-new-exposure inherited','winner/PnL excluded from rule and gates','no dream fill','no 8781']}
        Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
