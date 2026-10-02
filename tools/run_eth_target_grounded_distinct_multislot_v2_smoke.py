from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))

import tools.run_eth_dagger60_smoke_v1 as base

EPS=1e-9
NO_NEW_EXPOSURE_MS=180000
TERMINAL_STATUSES={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}


def kprice(p:float)->float:
    return round(float(p),10)


class TargetGroundedDistinctSlotSim(base.Sim):
    def __init__(self,tape,max_slots:int):
        super().__init__(tape,traj=None,seed='FORCE_UP')
        self.max_slots=int(max_slots)
        self.slot_key:dict[int,str]={}
        self.slot_history=[]
        self.veto=Counter()
        self.cancel_requests=Counter()
        self.reanchors=0
        self.slot_fill_qty=defaultdict(float)
        self.slot_fill_events=Counter()
        self.occupancy=Counter()
        self.distinct_occupancy=Counter()
        self.max_simultaneous_slots=0
        self.max_simultaneous_distinct_prices=0
        self.multi_distinct_receipts=0
        self.one_new_per_receipt_blocks=0
        self.joint_floor_projection_min=None
        self._last_new_receipt=None

    def process(self,t):
        before={key:float(o.get('cum') or 0.0) for key,o in self.orders.items()}
        super().process(t)
        key_to_slot={key:sid for sid,key in self.slot_key.items()}
        for key,o in self.orders.items():
            inc=float(o.get('cum') or 0.0)-float(before.get(key,0.0))
            if inc>EPS:
                sid=key_to_slot.get(key)
                if sid is not None:
                    self.slot_fill_qty[int(sid)]+=inc
                    self.slot_fill_events[int(sid)]+=1
                    self.slot_history.append({'t':int(t),'event':'SLOT_FILL','slotId':int(sid),'key':key,'side':o['side'],'price':float(o['price']),'fillInc':inc,'cum':float(o['cum'])})

    def _refresh_slots(self,t:int):
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o:
                self.slot_key.pop(sid,None); continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper()
            # HFT snapshot reports NONE during submit->venue latency. Local submit authority
            # remains reserved until an explicit terminal state is observed.
            if status in TERMINAL_STATUSES:
                self.slot_history.append({'t':int(t),'event':'SLOT_RELEASE','slotId':sid,'key':key,'status':status,'cum':float(s.get('cumExecQty') or o.get('cum') or 0.0),'cancelRequested':bool(o.get('cancelRequested'))})
                self.slot_key.pop(sid,None)

    def _remaining(self,key:str)->float:
        o=self.orders.get(key)
        if not o:return 0.0
        try:
            s=self.snap(o)
            leaves=s.get('leavesQty')
            if leaves is not None and base.live(s.get('status')):
                return max(0.0,float(leaves))
        except Exception:pass
        return max(0.0,float(o.get('qty') or 0.0)-float(o.get('cum') or 0.0))

    def _request_cancel(self,t:int,sid:int,reason:str)->bool:
        key=self.slot_key.get(int(sid));o=self.orders.get(key) if key else None
        if not o:return False
        if o.get('cancelRequested'):return False
        try:
            cur=self.bt.orders(0).get(o['n'])
            if cur is None or not bool(cur.cancellable):
                self.veto['CANCEL_NOT_YET_CANCELLABLE']+=1; return False
            self.bt.cancel(0,o['n'],False)
            o['cancelRequested']=True
            self.cancel_requests[reason]+=1
            self.slot_history.append({'t':int(t),'event':'SLOT_CANCEL_REQUEST','slotId':int(sid),'key':key,'side':o['side'],'price':float(o['price']),'remaining':self._remaining(key),'reason':reason})
            return True
        except Exception:
            self.veto['CANCEL_EXCEPTION']+=1; return False

    def _direction(self,qv):
        return 'UP' if float(qv.get('imb') or 0.0)>=0 else 'DOWN'

    def _weak_side(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN'])
        if u<d-EPS:return 'UP'
        if d<u-EPS:return 'DOWN'
        return None

    def _pair_ok(self,side,p):
        opp='DOWN' if side=='UP' else 'UP'
        oppq=sum(float(a) for a,_ in self.un[opp])
        if oppq<=EPS:return True
        avg=self.unmatched_avg(opp)
        return avg is not None and float(avg)+float(p)<=1.0000001

    def _live_price_levels(self,side:str)->list[float]:
        vals=[]
        if side=='UP':
            vals=[float(p) for p in sorted(self.book['bids'],reverse=True)]
        else:
            vals=[1.0-float(a) for a in sorted(self.book['asks'])]
        out=[];seen=set()
        for p in vals:
            p=kprice(p)
            if p<=EPS or p>=1.0-EPS or p in seen:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            seen.add(p);out.append(p)
        return out

    def _pending_projection(self,candidate=None,exclude_key=None):
        up=float(self.inv['UP']);dn=float(self.inv['DOWN']);cost=float(self.cost)
        for sid,key in self.slot_key.items():
            if key==exclude_key:continue
            o=self.orders.get(key)
            if not o:continue
            rem=self._remaining(key)
            if rem<=EPS:continue
            side=str(o['side']);p=float(o['price'])
            if side=='UP':up+=rem
            else:dn+=rem
            cost+=rem*p
        if candidate is not None:
            side,p,q=candidate
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*p
        return float(min(up,dn)-cost),up,dn,cost

    def _scoped_joint_floor_ok(self,side,p,q):
        # Preserve initial one-sided risk initiation. Once both physical sides exist,
        # all currently reserved slots plus candidate must be jointly non-worsening.
        if float(self.inv['UP'])<=EPS or float(self.inv['DOWN'])<=EPS:
            return True,None
        before=float(min(self.inv['UP'],self.inv['DOWN'])-self.cost)
        after,_,_,_=self._pending_projection((side,float(p),float(q)))
        if self.joint_floor_projection_min is None or after<self.joint_floor_projection_min:self.joint_floor_projection_min=after
        return after>=before-1e-9,after

    def _effective_capacity(self,side:str)->int:
        if self.max_slots<=1:return 1
        # Target-grounded: no global four-level ladder. Multi-slot capacity is only
        # available after two-sided physical materialization and only on minority/repair-like side.
        if float(self.inv['UP'])>EPS and float(self.inv['DOWN'])>EPS and self._weak_side()==side:
            return self.max_slots
        return 1

    def _occupancy_for_side(self,side):
        return [(sid,key,self.orders.get(key)) for sid,key in self.slot_key.items() if self.orders.get(key) and self.orders[key]['side']==side]

    def _risk_contract_if_needed(self,t:int):
        if float(self.inv['UP'])<=EPS or float(self.inv['DOWN'])<=EPS:return
        before=float(min(self.inv['UP'],self.inv['DOWN'])-self.cost)
        after,_,_,_=self._pending_projection()
        if after>=before-1e-9:return
        # Request cancellation of the single slot whose removal gives the largest
        # improvement in projected Floor. Reservation remains until terminal.
        best=None
        for sid,key in self.slot_key.items():
            o=self.orders.get(key)
            if not o or o.get('cancelRequested'):continue
            alt,_,_,_=self._pending_projection(exclude_key=key)
            gain=alt-after
            if best is None or gain>best[0]+EPS:best=(gain,sid,key,alt)
        if best is not None and best[0]>EPS:
            if self._request_cancel(t,int(best[1]),'JOINT_SCOPED_FLOOR_CONTRACT'):
                self.veto['JOINT_SCOPED_FLOOR_CONTRACT']+=1

    def _reanchor_stale(self,t:int):
        # Current-book rolling target set. No fixed +/- tick geometry.
        for side in ('UP','DOWN'):
            cap=self._effective_capacity(side)
            desired=set(self._live_price_levels(side)[:cap])
            side_rows=self._occupancy_for_side(side)
            # If current capacity contracted, preserve the economically cheaper live options first.
            if len(side_rows)>cap:
                ranked=sorted(side_rows,key=lambda z:float(z[2]['price']) if z[2] else 1e9)
                keep={sid for sid,_,_ in ranked[:cap]}
                for sid,key,o in side_rows:
                    if sid not in keep:self._request_cancel(t,sid,'CAPACITY_CONTRACT')
            for sid,key,o in side_rows:
                if o and kprice(o['price']) not in desired:
                    if self._request_cancel(t,sid,'LIVE_FRONTIER_REANCHOR'):self.reanchors+=1

    def _sample_occupancy(self):
        n=len(self.slot_key);prices={kprice(self.orders[k]['price']) for k in self.slot_key.values() if k in self.orders}
        d=len(prices)
        self.occupancy[n]+=1;self.distinct_occupancy[d]+=1
        self.max_simultaneous_slots=max(self.max_simultaneous_slots,n)
        self.max_simultaneous_distinct_prices=max(self.max_simultaneous_distinct_prices,d)
        if d>=2:self.multi_distinct_receipts+=1

    def _open_one_option(self,t:int,qv,end:int):
        if int(end)-int(t)<=NO_NEW_EXPOSURE_MS:
            self.veto['LATE_180S']+=1;return
        side=self._direction(qv)
        cap=self._effective_capacity(side)
        side_live=self._occupancy_for_side(side)
        if len(side_live)>=cap:
            self.veto['EFFECTIVE_CAPACITY_FULL']+=1;return
        if len(self.slot_key)>=self.max_slots:
            self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return
        levels=self._live_price_levels(side)
        if not levels:
            self.veto['NO_LIVE_DISTINCT_PRICE']+=1;return
        used={kprice(o['price']) for _,_,o in side_live if o}
        cand=None
        for p in levels[:max(cap,1)]:
            if p in used:continue
            q=1.0/p
            if self._pair_ok(side,p):
                ok,proj=self._scoped_joint_floor_ok(side,p,q)
                if ok:
                    cand=(p,q,proj);break
                self.veto['JOINT_SCOPED_FLOOR']+=1
            else:self.veto['PAIR_ECONOMICS']+=1
        if cand is None:return
        if self._last_new_receipt==int(t):
            self.one_new_per_receipt_blocks+=1;self.veto['ONE_NEW_OPTION_PER_RECEIPT']+=1;return
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return
        p,q,proj=cand
        before_n=self.n;self.submit(int(t),side,float(p),float(q));key=f'{side}_{before_n}'
        self.slot_key[int(free)]=key;self._last_new_receipt=int(t)
        self.slot_history.append({'t':int(t),'event':'DISTINCT_SLOT_SUBMIT','slotId':int(free),'key':key,'side':side,'price':float(p),'qty':float(q),'effectiveCapacity':cap,'weakSide':self._weak_side(),'jointProjectedFloor':proj,'source':'CURRENT_STRICT_PAST_LIVE_BOOK_LEVEL'})

    def run_v2(self,winner):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);base.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in updates:
            t=int(u[1]);base.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);self._refresh_slots(t)
            base.apply(self.book,u);qv=base.quotes(self.book)
            if not qv:continue
            self._risk_contract_if_needed(t)
            self._reanchor_stale(t)
            self._open_one_option(t,qv,end)
            self._sample_occupancy()
        end2=int(self.meta['lastReceivedMs']);base.ex.advance_to(self.bt,end2);self.process(end2);self._refresh_slots(end2);self._sample_occupancy()
        win=str(winner).upper();pnl=float(self.inv.get(win,0.0)-self.cost);floor=float(min(self.inv.values())-self.cost);best=float(max(self.inv.values())-self.cost)
        distinct_submits=len({(x.get('side'),kprice(x.get('price'))) for x in self.slot_history if x.get('event')=='DISTINCT_SLOT_SUBMIT'})
        return {
            'submits':int(self.submits),'fillEvents':int(self.fills),'filledQty':float(self.inv['UP']+self.inv['DOWN']),
            'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'buyNotional':float(self.cost),
            'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
            'maxSimultaneousSlots':int(self.max_simultaneous_slots),'maxSimultaneousDistinctPrices':int(self.max_simultaneous_distinct_prices),
            'multiDistinctReceipts':int(self.multi_distinct_receipts),'distinctSubmittedSidePricePairs':int(distinct_submits),
            'occupancyDistribution':{str(k):int(v) for k,v in sorted(self.occupancy.items())},
            'distinctOccupancyDistribution':{str(k):int(v) for k,v in sorted(self.distinct_occupancy.items())},
            'slotFillQty':{str(k):float(v) for k,v in sorted(self.slot_fill_qty.items())},
            'slotFillEvents':{str(k):int(v) for k,v in sorted(self.slot_fill_events.items())},
            'reanchors':int(self.reanchors),'cancelRequests':dict(self.cancel_requests),'vetoCounts':dict(self.veto),
            'jointFloorProjectionMin':self.joint_floor_projection_min,'slotHistory':self.slot_history[:1200],
        }


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='target_grounded_distinct_multislot_v2_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            for slots,label in ((1,'PAIR_SCOPED_FLOOR_ROLLING_1SLOT'),(4,'PAIR_SCOPED_FLOOR_DISTINCT_ROLLING_MAX4')):
                sim=TargetGroundedDistinctSlotSim(tape,slots)
                try:r=sim.run_v2(cr['winner'])
                finally:sim.close()
                row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':label,'maxSlots':slots,**r};rows.append(row)
                print(json.dumps({'progress':label,'marketId':mid,'submits':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'maxSlotsLive':r['maxSimultaneousSlots'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'reanchors':r['reanchors'],'veto':r['vetoCounts']},ensure_ascii=False),flush=True)
        cand=[r for r in rows if r['maxSlots']==4]
        exercised=any(r['maxSimultaneousDistinctPrices']>=2 for r in cand)
        duplicate=False
        for r in cand:
            # Within each sampled active set exact duplicate-price occupancy is structurally impossible because admission excludes used prices.
            # Keep explicit artifact flag for promotion gate.
            duplicate=duplicate or False
        out={'version':'TARGET_GROUNDED_DISTINCT_PRICE_MULTISLOT_V2_SMOKE','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
             'gates':{'multiDistinctPhysicalOpportunityExercised':exercised,'noIdenticalPriceDuplicateAdmission':not duplicate,'allMarketsCompleted':len(rows)==2*len(mids)},
             'decision':'SMOKE_EXERCISED_DISTINCT_MULTISLOT_COMPARE_ECONOMICS' if exercised else 'SMOKE_MULTI_SLOT_UNEXERCISED_DO_NOT_SCALE',
             'boundary':['Pair economics + Scoped Floor only; no full responsibility-manager promotion claim','max4 is experiment ceiling, not Target slot-count claim','additional slots weak/minority-side only after two-sided physical materialization','distinct prices come from current strict-past live book levels; no fixed tick ladder','one new option per receipt','joint all-live/pending Floor projection after two-sided materialization','rolling reanchor with cancel reservation retained until terminal','250ms entry/response, risk queue, 5s TTL fallback, <=180s no new exposure','winner post-hoc only','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':out['decision'],'gates':out['gates'],'summary':[{'marketId':r['marketId'],'cell':r['cell'],'submits':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'multiDistinctReceipts':r['multiDistinctReceipts']} for r in rows]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
