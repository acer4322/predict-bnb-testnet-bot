from __future__ import annotations
import argparse, json, tempfile, zipfile, shutil, sys, os, math, threading, time, importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_v34_path=Path(__file__).with_name('run_eth_repair_v34e_current_parent_reconnect_shadow.py')
_spec=importlib.util.spec_from_file_location('eth_v34e_staged',_v34_path)
if _spec is None or _spec.loader is None: raise ImportError(f'cannot load V34E from {_v34_path}')
v34=importlib.util.module_from_spec(_spec); sys.modules[_spec.name]=v34; _spec.loader.exec_module(v34)
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9
ACTIVE_TAKE_WINDOW_MS=500

class V36EventConfirmedActive(v34.V34ReadinessShadow):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.armFillBase={}
        self.hardConfirmed=set()
        self.activeByParent={}
        self.activeEvents=[]
        self.activeSubmitCount=0
        self.activeFillQty=0.0
        self.sharedRealizedOverfill=0.0
        self.passiveCancelBeforeActiveFill=0
        self.passiveResizeAfterActiveFill=0
        self.hardEventConfirmedCount=0
        self.blockedPaymentProgress=0
        self.blockedNoLegalSlice=0
        self.floorFillDeltas=[]

    def _parent_actual_fill(self,pid:int)->float:
        s=0.0
        for e in getattr(self,'carrierLedger',{}).values():
            try:
                if int(e.get('parentId'))==int(pid): s+=float(e.get('actualFilled') or 0.0)
            except Exception:
                pass
        return s

    def _arm_diag(self,t):
        ev=super()._arm_diag(t)
        if ev is not None:
            pid=int(ev['parentId'])
            self.armFillBase.setdefault(pid,self._parent_actual_fill(pid))
        return ev

    def _current_payoffs(self):
        floor,u,d,cost=self._raw_floor()
        return {'floor':float(floor),'up':float(u-cost),'down':float(d-cost),'best':float(max(u,d)-cost),'gap':float(abs(u-d))}

    def _base_repair_rows(self):
        return super().lane_unresolved('REPAIR')

    def _find_passive(self,pid:int):
        cand=[]
        for key,e,rem in self._base_repair_rows():
            try:
                if int(e.get('parentId'))!=pid or float(rem)<=EPS: continue
            except Exception:
                continue
            o=self.orders.get(key)
            if not o or str(e.get('lane') or '').startswith('ACTIVE_'): continue
            live=False
            try: live=bool(v1.live(self.snap(o).get('status')))
            except Exception: pass
            if live: cand.append((int(o.get('placed') or 0),key,e,float(rem),o))
        if not cand: return None
        cand.sort()
        _,key,e,rem,o=cand[-1]
        return key,e,rem,o

    def _submit_active(self,t,pid,passive_key,side,ask,q,objective_id):
        n=self.n; self.n+=1
        native_side,native_price=v1.ex.native_order(side,float(ask))
        try:
            if native_side=='BUY': rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
            else: rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
        except Exception:
            return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':float(ask),'qty':float(q),'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'REPAIR','objective_id':objective_id,'execution_role':'TAKER_ACTIVE_SHARED'}
        self.placeHist.append((int(t),side,float(q),float(ask))); self.submits+=1
        self.localPending[side][key]={'remaining':float(q),'submitted':int(t)}
        self.submitRoleObserved[key]='REPAIR'; self.submitRoleTruth[key]='REPAIR'; self.submitRoleAuthorized[key]='REPAIR'
        self.carrierLedger[key]={'key':key,'side':side,'objectiveId':objective_id,'objectiveRole':'REPAIR','submittedQty':float(q),'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':pid,'lane':'ACTIVE_REPAIR_SHARED'}
        self.submitTrace.append({'t':int(t),'side':side,'qty':float(q),'price':float(ask),'pendingRole':'REPAIR','parentId':pid,'lane':'ACTIVE_REPAIR_SHARED'})
        pe=self.carrierLedger.get(passive_key,{})
        self.activeByParent[pid]={'key':key,'passiveKey':passive_key,'side':side,'qty':float(q),'submitAt':int(t),'parentId':pid,'objectiveId':objective_id,'submitRc':rc,'fillSeen':0.0,'passiveBaseActual':float(pe.get('actualFilled') or 0.0),'maxPassiveAfter':0.0,'resizeDone':False,'cancelAt':None,'floorBefore':self._current_payoffs()['floor'],'floorAfter':None}
        self.activeSubmitCount+=1
        self.activeEvents.append({'event':'V36_ACTIVE_SHARED_SUBMIT','t':int(t),'parentId':pid,'side':side,'ask':float(ask),'qty':float(q),'passiveKey':passive_key,'floorBefore':self.activeByParent[pid]['floorBefore'],'postArmOpportunityCount':len([x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]),'submitRc':rc})
        return True

    def _maybe_hard_active(self,t):
        rp=self.repairParent
        if rp is None: return False
        pid=int(rp.get('id')); side=rp.get('side')
        if pid not in self._armedParents or pid in self.activeByParent or pid in self.hardConfirmed or side not in ('UP','DOWN'): return False
        opp=[x for x in self.postArmCarrierOpportunities if int(x.get('parentId'))==pid]
        if len(opp)<2: return False
        first2=opp[:2]
        if any(bool(x.get('connected')) for x in first2): return False
        base=float(self.armFillBase.get(pid,self._parent_actual_fill(pid)))
        now=self._parent_actual_fill(pid)
        if now>base+EPS:
            self.blockedPaymentProgress+=1
            return False
        pay=self._current_payoffs()
        if pay['floor']>=-EPS or pay['gap']<=EPS: return False
        psv=self._find_passive(pid)
        if psv is None: return False
        passive_key,e,rem,o=psv
        qv=v1.quotes(self.book)
        if not qv or side not in qv or qv[side].get('ask') is None: return False
        ask=float(qv[side]['ask']); legal=1.0/ask if ask>EPS else math.inf
        q=min(float(legal),float(pay['gap']),float(rem))
        if q<=EPS or q+EPS<legal:
            self.blockedNoLegalSlice+=1
            return False
        self.hardConfirmed.add(pid); self.hardEventConfirmedCount+=1
        oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
        self.activeEvents.append({'event':'V36_HARD_EVENT_CONFIRMED','t':int(t),'parentId':pid,'side':side,'postArmOpportunityCount':len(opp),'firstTwoDisconnected':True,'parentFillAtArm':base,'parentFillNow':now,'floor':pay['floor'],'gap':pay['gap'],'passiveRemaining':rem,'liveAsk':ask,'legalMinSlice':legal})
        return self._submit_active(t,pid,passive_key,side,ask,q,oid)

    def lane_unresolved(self,role):
        rows=super().lane_unresolved(role)
        if role!='REPAIR' or not self.activeByParent: return rows
        out=[]
        for key,e,rem in rows:
            rr=float(rem)
            try: pid=int(e.get('parentId'))
            except Exception: pid=-1
            a=self.activeByParent.get(pid)
            if a and key==a.get('passiveKey'):
                ak=a.get('key'); ae=self.carrierLedger.get(ak,{}) ; af=float(ae.get('actualFilled') or 0.0)
                ao=self.orders.get(ak); livea=False
                try: livea=bool(ao and v1.live(self.snap(ao).get('status')))
                except Exception: pass
                shared_pending=max(0.0,float(a.get('qty') or 0.0)-af) if livea else 0.0
                rr=max(0.0,rr-shared_pending)
            if rr>EPS: out.append((key,e,rr))
        return out

    def _reconcile_active(self,t):
        for pid,a in list(self.activeByParent.items()):
            ak=a['key']; pk=a['passiveKey']; ae=self.carrierLedger.get(ak,{}); pe=self.carrierLedger.get(pk,{})
            af=float(ae.get('actualFilled') or 0.0); pf=max(0.0,float(pe.get('actualFilled') or 0.0)-float(a.get('passiveBaseActual') or 0.0))
            old=float(a.get('fillSeen') or 0.0)
            if af>old+EPS:
                before=float(a.get('floorBefore')) if old<=EPS else float(a.get('floorAfter') if a.get('floorAfter') is not None else self._current_payoffs()['floor'])
                inc=af-old; a['fillSeen']=af; self.activeFillQty+=inc
                after=self._current_payoffs()['floor']; a['floorAfter']=after
                self.floorFillDeltas.append(after-before)
                self.activeEvents.append({'event':'V36_ACTIVE_SHARED_FILL','t':int(t),'parentId':pid,'incQty':inc,'cumQty':af,'floorBeforeObserved':before,'floorAfterObserved':after,'floorDelta':after-before,'passiveFillAfterArm':pf})
            a['maxPassiveAfter']=max(float(a.get('maxPassiveAfter') or 0.0),pf)
            realized=af+pf
            self.sharedRealizedOverfill=max(self.sharedRealizedOverfill,max(0.0,realized-float(a.get('qty') or 0.0)))
            if af>EPS and not a.get('resizeDone'):
                po=self.orders.get(pk); prem=0.0
                if po:
                    try: prem=max(0.0,float(po.get('qty') or 0.0)-float(pe.get('actualFilled') or 0.0))
                    except Exception: pass
                if prem>EPS:
                    if self._cancel_key(t,pk):
                        self.passiveResizeAfterActiveFill+=1
                        self.activeEvents.append({'event':'V36_PASSIVE_RESIZE_CANCEL_AFTER_ACTIVE_FILL','t':int(t),'parentId':pid,'passiveKey':pk,'passiveRemaining':prem,'activeFilled':af})
                a['resizeDone']=True
            if pf>EPS and af<=EPS:
                ao=self.orders.get(ak)
                try:
                    if ao and v1.live(self.snap(ao).get('status')) and ak not in self.cancelRequestedAt:
                        if self._cancel_key(t,ak): self.activeEvents.append({'event':'V36_ACTIVE_CANCEL_AFTER_PASSIVE_SHARED_FILL','t':int(t),'parentId':pid,'passiveFilled':pf})
                except Exception: pass

    def process(self,t):
        super().process(t)
        self._reconcile_active(t)
        self._maybe_hard_active(t)

    def cancel_expired(self,t):
        super().cancel_expired(t)
        self._reconcile_active(t)
        self._maybe_hard_active(t)
        for pid,a in list(self.activeByParent.items()):
            ak=a['key']; ao=self.orders.get(ak)
            if not ao: continue
            try:
                livea=bool(v1.live(self.snap(ao).get('status')))
            except Exception:
                livea=False
            if livea and int(t)-int(a['submitAt'])>=ACTIVE_TAKE_WINDOW_MS and ak not in self.cancelRequestedAt:
                if self._cancel_key(t,ak):
                    a['cancelAt']=int(t)
                    self.activeEvents.append({'event':'V36_ACTIVE_SHARED_CANCEL_REMAINDER','t':int(t),'parentId':pid,'ageMs':int(t)-int(a['submitAt'])})

    def run_exam_v36(self,models,winner):
        r=super().run_exam_v34(models,winner)
        r.update({
            'v36HardEventConfirmedCount':self.hardEventConfirmedCount,
            'v36ActiveSubmitCount':self.activeSubmitCount,
            'v36ActiveFillQty':self.activeFillQty,
            'v36SharedRealizedOverfill':self.sharedRealizedOverfill,
            'v36PassiveCancelBeforeActiveFill':self.passiveCancelBeforeActiveFill,
            'v36PassiveResizeAfterActiveFill':self.passiveResizeAfterActiveFill,
            'v36BlockedPaymentProgress':self.blockedPaymentProgress,
            'v36BlockedNoLegalSlice':self.blockedNoLegalSlice,
            'v36FloorFillDeltas':self.floorFillDeltas,
            'v36ActiveEvents':self.activeEvents[:200]
        })
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','market-ids']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='eth_v36_active_'))
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(10): print(json.dumps({'heartbeat':'V36_RUN','ts':time.time()}),flush=True)
    threading.Thread(target=heartbeat,daemon=True).start(); print(json.dumps({'heartbeat':'V36_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']; by={int(r['marketId']):r for r in cohort}
        models,life,cap,tim,econ,price,sur=v34.v30.load_runtime(a); rows=[]
        for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
            cr=by[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            b=v34.V34ReadinessShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try: br=b.run_exam_v34(models,cr['winner'])
            finally: b.close()
            s=V36EventConfirmedActive(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
            try: ar=s.run_exam_v36(models,cr['winner'])
            finally: s.close()
            rows.append({'marketId':mid,'baseline':br,'active':ar})
            print(json.dumps({'marketId':mid,'hard':ar['v36HardEventConfirmedCount'],'activeSubmit':ar['v36ActiveSubmitCount'],'activeFillQty':ar['v36ActiveFillQty'],'baselineFloor':br['floor'],'activeFloor':ar['floor'],'baselineTerminalParents':br['v34ParentTerminalUnresolved'],'activeTerminalParents':ar['v34ParentTerminalUnresolved'],'floorFillDeltas':ar['v36FloorFillDeltas']},ensure_ascii=False),flush=True)
        def sm(side,key): return sum(float(x[side].get(key) or 0.0) for x in rows)
        triggered=[x for x in rows if int(x['active'].get('v36HardEventConfirmedCount') or 0)>0]
        harmful_fill=sum(1 for x in rows for d in x['active'].get('v36FloorFillDeltas',[]) if float(d)<-EPS)
        agg={
            'markets':len(rows),'triggeredMarkets':len(triggered),'hardEventConfirmed':int(sm('active','v36HardEventConfirmedCount')),
            'activeSubmits':int(sm('active','v36ActiveSubmitCount')),'activeFillQty':sm('active','v36ActiveFillQty'),
            'sharedRealizedOverfill':sm('active','v36SharedRealizedOverfill'),'truthMismatch':sm('active','authorizedSubmitWithTruthRoleMismatch'),
            'overOwned':sm('active','overOwnedSubmitViolations'),'repairDrift':sm('active','repairToExpandAtFirstFill'),
            'harmfulActiveFillEvents':harmful_fill,'baselineTerminalParents':int(sm('baseline','v34ParentTerminalUnresolved')),
            'activeTerminalParents':int(sm('active','v34ParentTerminalUnresolved')),
            'baselineFloorSum':sm('baseline','floor'),'activeFloorSum':sm('active','floor')
        }
        gates={
            'zeroSharedOverfill':agg['sharedRealizedOverfill']<=EPS,
            'zeroTruthMismatch':agg['truthMismatch']==0,
            'zeroOverOwned':agg['overOwned']==0,
            'zeroRepairDrift':agg['repairDrift']==0,
            'zeroHarmfulActiveFill':agg['harmfulActiveFillEvents']==0,
            'zeroPreFillPassiveCancel':sm('active','v36PassiveCancelBeforeActiveFill')==0
        }
        out={'version':'ETH_REPAIR_V36_EVENT_CONFIRMED_SHARED_ACTIVE_CHILD','researchOnly':True,'behaviorChange':True,'actionAuthorityScope':'consumed/fresh confirmatory HFT research only; never 8781','aggregate':agg,'gates':gates,'rows':rows,'boundary':['V34 soft-arm + two post-arm disconnected replacement materializations','zero same-parent Repair payment between arm and hard confirmation','no fixed age/tick/placement threshold','MIN_SLICE active sizing','shared passive+active Repair budget','500ms active window inherited from V33 execution mechanic','winner/PnL excluded from trigger','no dream fill','no 8781']}
        Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':all(gates.values()),'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
