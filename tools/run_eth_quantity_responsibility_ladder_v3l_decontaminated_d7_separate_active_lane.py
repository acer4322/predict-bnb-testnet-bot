from __future__ import annotations
import argparse,json,math,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

base=v3b.base; EPS=v3b.EPS; TICK=v3b.TICK; ACTIVE_WINDOW_MS=v3b.ACTIVE_WINDOW_MS
TERMINAL_STATUS={'FILLED','CANCELED','EXPIRED','REJECTED'}

class DecontaminatedD7SeparateActiveLaneV3L(v3b.FifoAggregateResponsibilityLadderV3B):
    """Historical D7 capacity semantics on current Pair-Core + exact-FIFO.

    Four slot_key entries remain passive/distinct-price execution capacity.
    The single managed protected Active carrier uses one separate execution lane.
    Receipt-by-receipt ordinary Pair-Core scheduling is otherwise unchanged.
    """
    def __init__(self,tape):
        super().__init__(tape)
        self.sep_active_keys=set()
        self.sep_active_max=0
        self.total_execution_lanes_max=0
        self.sep_events=[]
        self.natural_ordinary_while_active=[]
        self._last_sep_submit_t=None

    def _active_lane_live(self):
        live=[]
        for key in list(self.sep_active_keys):
            o=self.orders.get(key)
            if not o:continue
            try:
                s=self.snap(o); status=str(s.get('status') or '').upper()
                # NONE means not yet observable at venue/frontier, not terminal.
                if status not in TERMINAL_STATUS: live.append(key)
            except Exception:
                live.append(key)
        return live

    def _record_lane_occupancy(self,t,event):
        active=len(self._active_lane_live())
        passive=len(self.slot_key)
        self.sep_active_max=max(self.sep_active_max,active)
        self.total_execution_lanes_max=max(self.total_execution_lanes_max,passive+active)
        self.sep_events.append({'t':int(t),'event':event,'passiveSlots':passive,'separateActiveLive':active,'totalExecutionLanes':passive+active})

    def _sample_occupancy(self):
        super()._sample_occupancy()
        active=len(self._active_lane_live())
        self.sep_active_max=max(self.sep_active_max,active)
        self.total_execution_lanes_max=max(self.total_execution_lanes_max,len(self.slot_key)+active)

    def _submit_role(self,t,side,role,p,q,proj,source):
        before=int(self.submits)
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        if ok and int(self.submits)>before and self._active_lane_live():
            key=f'{side}_{self.n-1}'
            self.natural_ordinary_while_active.append({'t':int(t),'key':key,'role':role,'side':side,'price':float(p),'qty':float(q),'passiveSlotsAfter':len(self.slot_key),'activeKeys':list(self._active_lane_live())})
        return ok

    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        if pnd is None or L is None:return False
        target=self._aggregate_outstanding_expand_side(pnd['targetExpandSide'])
        if target<=EPS:
            self.q_counter['activeSuppressedQueueSatisfied']+=1;self._complete_carrier(t,'QUEUE_SATISFIED_BEFORE_ACTIVE');return False
        if self._active_lane_live():
            self.q_counter['separateActiveLaneBusy']+=1;return False
        side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);limit=round(min(.99,ask+TICK),10);min_qty=1.0/limit
        source_remaining=float(pnd['sourceRemainingQty']);qty=min(source_remaining,target)
        if qty+EPS<min_qty:
            self.q_counter['activeDeferredAggregateBelowMinimum']+=1
            self.q_events.append({'event':'QTY_FIFO_ACTIVE_DEFERRED_BELOW_MINIMUM','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'targetExpandSide':pnd['targetExpandSide'],'aggregateRemaining':target,'sourceRemainingQty':source_remaining,'minimumActiveQty':min_qty})
            self._complete_carrier(t,'ACTIVE_DEFERRED_AGGREGATE_BELOW_MINIMUM');return False
        n=self.n;self.n+=1;ex=base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception:
            self.q_counter['activeSubmitException']+=1;return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,limit));self.submits+=1
        # Deliberately NOT inserted into slot_key: historical D7 used a separate Active lane.
        self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.sep_active_keys.add(key);self._last_sep_submit_t=int(t)
        L.update({'route':'ACTIVE','activeKey':key,'activeSubmittedAt':int(t),'activeSeparateLane':True})
        self.q_active_remainder_cancel=False;self.q_counter['managedActiveSubmits']+=1;self.q_counter['separateActiveLaneSubmits']+=1
        self.q_events.append({'event':'QTY_FIFO_MANAGED_ACTIVE_SUBMIT','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'targetExpandSide':pnd['targetExpandSide'],'sourceKey':pnd['sourceKey'],'key':key,'side':side,'role':role,'decisionAsk':ask,'limitPrice':limit,'qty':qty,'targetAggregateRemaining':target,'minimumActiveQty':min_qty,'submitRc':rc,'separateActiveLane':True,'passiveSlotsAtSubmit':len(self.slot_key)})
        self._record_lane_occupancy(t,'SEPARATE_ACTIVE_LANE_SUBMIT')
        return True

    def _manage_active_remainder(self,t):
        if self._inside_parent_process:return
        L=self.q_ladder
        if not L or L.get('route')!='ACTIVE' or not L.get('activeSeparateLane') or self.q_active_remainder_cancel:return
        key=L.get('activeKey');o=self.orders.get(key)
        if not o:return
        try:s=self.snap(o);live=base.v2.base.live(s.get('status'))
        except Exception:return
        if live and int(t)-int(L.get('activeSubmittedAt') or t)>=ACTIVE_WINDOW_MS and not o.get('cancelRequested'):
            co=self.bt.orders(0).get(o['n'])
            if co is not None and bool(co.cancellable):
                try:
                    self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.q_active_remainder_cancel=True
                    self.q_counter['activeRemainderCancels']+=1
                    self.sep_events.append({'t':int(t),'event':'SEPARATE_ACTIVE_REMAINDER_CANCEL','key':key})
                except Exception:pass

    def _refresh_slots(self,t):
        # Refresh passive/distinct-price slots with inherited Pair-Core semantics.
        super()._refresh_slots(t)
        L=self.q_ladder
        if not L or L.get('route')!='ACTIVE' or not L.get('activeSeparateLane'):return
        key=L.get('activeKey');o=self.orders.get(key)
        if not o:return
        try:s=self.snap(o);status=str(s.get('status') or '').upper()
        except Exception:return
        if status not in TERMINAL_STATUS:return
        cum=float(o.get('cum') or 0.0);target=self._aggregate_outstanding_expand_side(L['targetExpandSide'])
        self.q_events.append({'event':'QTY_FIFO_MANAGED_ACTIVE_TERMINAL','t':int(t),'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key,'status':status,'cum':cum,'targetAggregateRemaining':target,'separateActiveLane':True})
        if cum>EPS:self.q_counter['managedActivePhysicalSuccess']+=1
        else:self.q_counter['managedActiveZeroFillTerminal']+=1
        self.sep_active_keys.discard(key)
        self._record_lane_occupancy(t,'SEPARATE_ACTIVE_LANE_TERMINAL')
        self._complete_carrier(t,'ACTIVE_TERMINAL')

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3L']='DECONTAMINATED_D7_SEPARATE_ACTIVE_LANE'
        r['quantityLadderVersion']='V3L_DECONTAMINATED_D7_SEPARATE_ACTIVE_LANE'
        r['separateActiveLane']={'maxConcurrent':int(self.sep_active_max),'maxTotalExecutionLanes':int(self.total_execution_lanes_max),'events':self.sep_events,'naturalOrdinaryWhileActive':self.natural_ordinary_while_active}
        return r

def parity(a,b):
    for k in ['submits','fillEvents','fillSideAlternations','upQty','downQty','buyNotional','floor','pnlDiagnosticOnly']:
        if abs(float(a.get(k) or 0)-float(b.get(k) or 0))>1e-10:return False
    return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3l_sep_active_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();mr=[]
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3L_SEPARATE_ACTIVE_LANE',DecontaminatedD7SeparateActiveLaneV3L)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
                row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row);mr.append(row)
            A,B=mr
            print(json.dumps({'progress':i,'marketId':mid,'parity':parity(A,B),'A':{'pnl':A['pnlDiagnosticOnly'],'floor':A['floor'],'fills':A['fillEvents'],'submits':A['submits'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'floor':B['floor'],'fills':B['fillEvents'],'submits':B['submits'],'alts':B['fillSideAlternations'],'passiveMaxSlots':B['maxSimultaneousSlots'],'sep':B.get('separateActiveLane')},'ledger':B['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    A={r['marketId']:r for r in rows if r['cell']=='A_V3B_FIFO_AGGREGATE'};B={r['marketId']:r for r in rows if r['cell']=='B_V3L_SEPARATE_ACTIVE_LANE'}
    cmp=[]
    for mid in mids:
        x=A[mid];y=B[mid];s=y.get('separateActiveLane') or {}
        cmp.append({'marketId':mid,'parity':parity(x,y),'pnlDelta':float(y['pnlDiagnosticOnly'])-float(x['pnlDiagnosticOnly']),'floorDelta':float(y['floor'])-float(x['floor']),'fillDelta':int(y['fillEvents'])-int(x['fillEvents']),'submitDelta':int(y['submits'])-int(x['submits']),'alternationDelta':int(y['fillSideAlternations'])-int(x['fillSideAlternations']),'passiveMaxSlots':int(y['maxSimultaneousSlots']),'separateActiveMaxConcurrent':int(s.get('maxConcurrent') or 0),'maxTotalExecutionLanes':int(s.get('maxTotalExecutionLanes') or 0),'naturalOrdinaryWhileActive':len(s.get('naturalOrdinaryWhileActive') or [])})
    out={'version':'PAIR_CORE_DECONTAMINATED_D7_SEPARATE_ACTIVE_LANE_V3L','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'summary':{'A_V3B_FIFO_AGGREGATE':v3b.agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3L_SEPARATE_ACTIVE_LANE':v3b.agg(rows,'B_V3L_SEPARATE_ACTIVE_LANE')},'boundary':['historical D7 separate Active lane decontamination','four passive/distinct-price slots retained','one managed Active lane separate from slot_key','no same-receipt ordinary forcing','receipt-by-receipt Pair-Core scheduler unchanged','protected Active timing/price/qty/500ms semantics retained','exact FIFO retained','no old Floor/recoverability/quota/scopeGeneration/monetary-credit gates','winner/PnL posthoc only','realistic HFT/no dream fill/no 8781/no NEW24-B']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'comparison':cmp},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
