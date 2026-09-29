"""Research-only causal route relay probe on structural market 1823755.

A = frozen inside-spread + TTL-hold result, no relay.
B = after designated source carrier terminal zero-fill, one same-intent Passive relay
    at current strict-past closest non-crossing frontier, same source remaining qty.
C = after the same terminal zero-fill, one same-intent Active relay at current ask,
    GTC LIMIT, same source remaining qty; live remainder cancelled after 500ms.

Diagnostic only. No new responsibility, no side/role authority, no quantity increase,
no automatic scale. Local pair<=1 may be exceeded and is logged explicitly.
"""
from __future__ import annotations
import argparse,json,math,tempfile,zipfile,sys
from pathlib import Path

ROOT=Path.cwd() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import importlib.util
_STAGED_ISP=Path.cwd()/'.lan_worker_v1'/'staging'/'run_inside_spread_maker_priority_one_shot_probe_v1.py'
if _STAGED_ISP.exists():
    _sp=importlib.util.spec_from_file_location('staged_inside_spread_probe',_STAGED_ISP);isp=importlib.util.module_from_spec(_sp);_sp.loader.exec_module(isp)
else:
    from tools import run_inside_spread_maker_priority_one_shot_probe_v1 as isp
from tools import run_gpt6_detail_intelligence_v1_external as v1

EPS=1e-9
TICK=0.01
ACTIVE_WINDOW_MS=500
TERMINAL={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}
CELLS=(
    'A_INSIDE_SPREAD_TTL_NO_RELAY',
    'B_SECOND_PASSIVE_FRONTIER_TTL',
    'C_ACTIVE_ASK_RELAY_500MS',
)

class RouteRelaySim(isp.InsideSpreadProbeSim):
    def __init__(self,tape,relay_cell,trace_path):
        super().__init__(tape,isp.CELLS[2],trace_path)
        self.relay_cell=relay_cell
        self.source_terminal_handled=False
        self.pending_relay=None
        self.relay_used=False
        self.relay_key=None
        self.relay_meta=None
        self.relay_cancel_suppressed=0
        self.active_submitted_at=None
        self.active_cancel_requested=False
        self.relay_block_reason=None

    def _capture_source_terminal(self,t):
        if self.source_terminal_handled or not self.probe_key:return
        o=self.orders.get(self.probe_key)
        if not o:return
        try:s=self.snap(o)
        except Exception:return
        st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
        if st not in TERMINAL:return
        self.source_terminal_handled=True
        if st=='FILLED' or cum>EPS:return
        source_qty=float((self.probe_candidate or {}).get('probeQty') or o.get('qty') or 0.0)
        remaining=max(0.0,source_qty-cum)
        if remaining<=EPS:return
        self.pending_relay={'sourceKey':self.probe_key,'sourceTerminalAt':int(t),'sourceStatus':st,
                            'side':str(o['side']),'role':str(self.key_role.get(self.probe_key,'UNKNOWN')),
                            'remainingQty':remaining,'sourcePrice':float(o['price'])}

    def _refresh_slots(self,t):
        self._capture_source_terminal(t)
        super()._refresh_slots(t)
        self._manage_active_remainder(t)

    def process(self,t):
        super().process(t)
        self._manage_active_remainder(t)

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(sid)
        if (self.relay_cell==CELLS[1] and key==self.relay_key and
            reason in {'CORE_INVALIDATED','SATELLITE_FRONTIER_REANCHOR'}):
            o=self.orders.get(key)
            if o is not None and int(t)-int(o['placed'])<int(isp.base.v2.base.TTL):
                self.relay_cancel_suppressed+=1
                return False
        return super()._request_cancel(t,sid,reason)

    def _pair_diag(self,side,price):
        opp='DOWN' if side=='UP' else 'UP'
        lots=[(float(q),float(p)) for q,p in self.un[opp]]
        q=sum(a for a,_ in lots)
        avg=(sum(a*p for a,p in lots)/q) if q>EPS else None
        return {'oppositeUnmatchedQty':q,'oppositeUnmatchedAverage':avg,
                'pairSum':(avg+float(price)) if avg is not None else None,
                'pairOverage':(avg+float(price)-1.0) if avg is not None else None}

    def _submit_passive_relay(self,t,qv):
        pnd=self.pending_relay;side=pnd['side'];role=pnd['role'];qty=float(pnd['remainingQty'])
        bid=float(qv[side]['bid']);ask=float(qv[side]['ask'])
        price=round(ask-TICK,10)
        if price<=bid+EPS:price=round(bid,10)
        if not (price>EPS and price<ask-EPS):
            self.relay_block_reason='NO_NONCROSSING_PASSIVE_PRICE';return False
        if round(price,10) in {round(float(x),10) for x in self._used_prices(side)}:
            self.relay_block_reason='PASSIVE_PRICE_ALREADY_OCCUPIED';return False
        before_n=self.n
        ok=self._submit_role(t,side,role,price,qty,None,'SAME_INTENT_SECOND_PASSIVE_FRONTIER_DIAGNOSTIC')
        if not ok:
            self.relay_block_reason='PASSIVE_SUBMIT_FAILED';return False
        key=f'{side}_{before_n}';self.relay_key=key;self.relay_used=True
        self.relay_meta={'route':'PASSIVE','sourceKey':pnd['sourceKey'],'submittedAt':int(t),'side':side,'role':role,
                         'price':price,'qty':qty,'bidAtRelay':bid,'askAtRelay':ask,**self._pair_diag(side,price)}
        if key in self.detail_outcomes:self.detail_outcomes[key]['route']='PASSIVE_RELAY'
        self.slot_history.append({'t':int(t),'event':'SAME_INTENT_PASSIVE_RELAY','sourceKey':pnd['sourceKey'],
                                  'key':key,'side':side,'role':role,'price':price,'qty':qty})
        return True

    def _submit_active_relay(self,t,qv):
        pnd=self.pending_relay;side=pnd['side'];role=pnd['role'];qty=float(pnd['remainingQty'])
        ask=float(qv[side]['ask']);bid=float(qv[side]['bid'])
        if len(self.slot_key)>=self.max_slots:
            self.relay_block_reason='NO_FREE_SLOT';return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:
            self.relay_block_reason='NO_FREE_SLOT';return False
        n=self.n;self.n+=1
        ex=isp.base.v2.base.ex
        native_side,native_price=ex.native_order(side,ask)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception as exc:
            self.relay_block_reason='ACTIVE_SUBMIT_EXCEPTION:'+str(exc);return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':ask,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,ask));self.submits+=1
        self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,ask,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        self.detail_outcomes[key]={'decisionId':self.detail_index,'key':key,'role':role,'side':side,'route':'ACTIVE_RELAY',
                                   'submittedAt':int(t),'submittedPrice':ask,'submittedQty':qty,'fills':[],'terminal':None}
        self.relay_key=key;self.relay_used=True;self.active_submitted_at=int(t)
        self.relay_meta={'route':'ACTIVE','sourceKey':pnd['sourceKey'],'submittedAt':int(t),'side':side,'role':role,
                         'price':ask,'qty':qty,'bidAtRelay':bid,'askAtRelay':ask,'submitRc':rc,**self._pair_diag(side,ask)}
        self.slot_history.append({'t':int(t),'event':'SAME_INTENT_ACTIVE_RELAY_SUBMIT','sourceKey':pnd['sourceKey'],
                                  'key':key,'slotId':int(free),'side':side,'role':role,'price':ask,'qty':qty,'submitRc':rc})
        return True

    def _manage_active_remainder(self,t):
        if self.relay_cell!=CELLS[2] or not self.relay_key or self.active_cancel_requested:return
        o=self.orders.get(self.relay_key)
        if not o or self.active_submitted_at is None:return
        try:s=self.snap(o)
        except Exception:return
        if isp.base.v2.base.live(s.get('status')) and int(t)-int(self.active_submitted_at)>=ACTIVE_WINDOW_MS:
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                try:
                    self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.active_cancel_requested=True
                    self.slot_history.append({'t':int(t),'event':'ACTIVE_RELAY_CANCEL_REMAINDER','key':self.relay_key})
                except Exception:pass

    def _open_one_option(self,t,qv,end):
        if self.pending_relay is not None and not self.relay_used and self.relay_cell!=CELLS[0]:
            if int(end)-int(t)<=isp.base.v2.NO_NEW_EXPOSURE_MS:
                self.relay_block_reason='LATE_180S';self.pending_relay=None
            else:
                ok=self._submit_passive_relay(t,qv) if self.relay_cell==CELLS[1] else self._submit_active_relay(t,qv)
                self.pending_relay=None
                if ok:return
        return super()._open_one_option(t,qv,end)

    def run_route(self):
        r=super().run_probe()
        outcome=next((x for x in r.get('orderOutcomes',[]) if x.get('key')==self.relay_key),None)
        if outcome is None and self.relay_key in self.detail_outcomes:outcome=self.detail_outcomes[self.relay_key]
        snap=None
        if self.relay_key and self.relay_key in self.orders:
            try:snap=self.snap(self.orders[self.relay_key])
            except Exception as exc:snap={'snapshotError':str(exc)}
        r.update({'routeRelayCell':self.relay_cell,'sourceTerminalHandled':self.source_terminal_handled,
                  'relayUsed':self.relay_used,'relayKey':self.relay_key,'relayMeta':self.relay_meta,
                  'relayOutcome':outcome,'relayFinalSnapshot':snap,'relayCancelSuppressed':self.relay_cancel_suppressed,
                  'relayBlockReason':self.relay_block_reason,'activeRemainderCancelRequested':self.active_cancel_requested,
                  'activeRouteImplemented':self.relay_cell==CELLS[2] and self.relay_used,'automaticScaleAllowed':False})
        return r

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):return set(a)==set(b) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-9)
    return a==b

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--bundle',required=True);ap.add_argument('--reference',required=True);ap.add_argument('--market-id',type=int,default=1823755);ap.add_argument('--output',required=True);a=ap.parse_args()
    if a.market_id!=1823755:ap.error('V1 preregistered only for 1823755')
    op=Path(a.output).resolve()
    if op.exists():ap.error('Do not overwrite result')
    ref=json.loads(Path(a.reference).read_text(encoding='utf-8'))
    old=ref
    if old.get('marketId')!=a.market_id or old.get('cell')!='C_ONE_SHOT_INSIDE_SPREAD_TTL_HOLD':
        raise RuntimeError('Reference must be frozen 1823755 replication3 C result')
    trace_dir=op.parent/(op.stem+'_traces');trace_dir.mkdir(parents=True,exist_ok=False)
    fields=('submits','fillEvents','filledQty','upQty','downQty','buyNotional','floor','best','fillSideAlternations',
            'twoSidedMaterialized','roleSubmits','roleFills','roleFillQty','reanchors','economicRepairQty','economicOverflowQty')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='route_relay_1823755_') as folder:
        root=Path(folder)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(r['marketId']):r for r in json.loads(z.read('cohort.json'))['rows']};z.extract(f'tapes/{a.market_id}.json.xz',root)
        tape=root/'tapes'/f'{a.market_id}.json.xz'
        if v1.sha256(tape)!=old['tapeSha256']:raise RuntimeError('Frozen tape mismatch')
        for cell in CELLS:
            tp=trace_dir/f'{a.market_id}_{cell}.jsonl';sim=RouteRelaySim(tape,cell,tp)
            try:r=sim.run_route()
            finally:sim.close()
            winner=str(cohort[a.market_id]['winner']).upper();r['pnlDiagnosticOnly']=r['upQty' if winner=='UP' else 'downQty']-r['buyNotional']
            row={'marketId':a.market_id,'cell':cell,'winnerPostHocOnly':winner,'tapeSha256':old['tapeSha256'],'decisionTracePath':str(tp),**r}
            if cell==CELLS[0]:
                row['baselineParity']={f:eq(row[f],old[f]) for f in fields}
                if not all(row['baselineParity'].values()):raise RuntimeError('A baseline parity failed')
            rows.append(row);v1.write_json(trace_dir/f'{a.market_id}_{cell}_result.json',row)
            ro=row.get('relayOutcome') or {};rm=row.get('relayMeta') or {}
            print(json.dumps({'cell':cell,'submits':row['submits'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],
                              'repairQty':row['economicRepairQty'],'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],
                              'relayUsed':row['relayUsed'],'route':rm.get('route'),'relayPrice':rm.get('price'),'relayQty':rm.get('qty'),
                              'relayPairSum':rm.get('pairSum'),'relayTerminal':(ro.get('terminal') or {}).get('status'),
                              'relayFillQty':sum(float(x.get('confirmedQty') or 0) for x in ro.get('fills',[])),
                              'relayRepairQty':sum(float(x.get('matchedRepairQty') or 0) for x in ro.get('fills',[])),
                              'relayOverflowQty':sum(float(x.get('overflowQty') or 0) for x in ro.get('fills',[])),
                              'relayBlockReason':row.get('relayBlockReason')},allow_nan=False),flush=True)
    v1.write_json(op,{'version':'PASSIVE_EXHAUSTION_SAME_INTENT_ROUTE_RELAY_1823755_V1','date':'2026-09-06','researchOnly':True,
                      'runtimeAuthority':False,'marketId':a.market_id,'rows':rows,'bundleSha256':v1.sha256(a.bundle),
                      'referenceSha256':v1.sha256(a.reference),'automaticScaleAllowed':False,
                      'boundary':['same designated source intent','source must terminal zero-fill before relay','one relay maximum','same side/role','relay qty <= source remaining qty','B Passive GTX; C Active GTC current ask','500ms active remainder window','<=180s retained','no Target runtime','winner posthoc only','no dream fill','no 8781','local pair>1 is diagnostic exception, not promotion authority']})
if __name__=='__main__':main()
