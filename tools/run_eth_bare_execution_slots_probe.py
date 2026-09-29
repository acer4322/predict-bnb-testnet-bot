from __future__ import annotations
import argparse,json,lzma,math,os,shutil,tempfile,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
# The LAN worker keeps the frozen hftbacktest 2.4.4 wheel unpacked in staging.
# Add it explicitly so this research-only probe uses the same native engine without mutating the worker venv.
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))

import tools.run_eth_dagger60_smoke_v1 as base

EPS=1e-9
NO_NEW_EXPOSURE_MS=180000

class BareSlotProbe(base.Sim):
    def __init__(self,tape,max_slots:int):
        super().__init__(tape,traj=None,seed='FORCE_UP')
        self.max_slots=int(max_slots)
        self.slot_key:dict[int,str]={}
        self.slot_submits=0
        self.slot_reopens=0
        self.slot_fill_events=[]
        self.slot_history=[]
        self._fill_event_count=0

    def record_fill(self,t,side,q,p):
        super().record_fill(t,side,q,p)
        self._fill_event_count+=1
        self.slot_fill_events.append({'t':int(t),'side':str(side),'qty':float(q),'price':float(p)})

    def _refresh_slots(self,t:int):
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o:
                self.slot_key.pop(sid,None); continue
            try: s=self.snap(o)
            except Exception: s={}
            status=s.get('status')
            if not base.live(status):
                self.slot_history.append({'t':int(t),'event':'SLOT_RELEASE','slotId':sid,'key':key,'status':status,'cum':float(s.get('cumExecQty') or o.get('cum') or 0.0)})
                self.slot_key.pop(sid,None)

    def _direction(self,qv):
        # Deliberately primitive/public-only action source. No strategy safety model.
        return 'UP' if float(qv.get('imb') or 0.0)>=0.0 else 'DOWN'

    def _open_free_slots(self,t:int,qv,end:int):
        if int(end)-int(t)<=NO_NEW_EXPOSURE_MS:
            return
        side=self._direction(qv)
        bid=float(qv[side]['bid']); ask=float(qv[side]['ask'])
        if bid<=EPS or bid>=1.0-EPS or ask<=bid+EPS:
            return
        for sid in range(1,self.max_slots+1):
            if sid in self.slot_key: continue
            # Same live bid anchor for all slots: isolate slot-count effect from price-fanout effect.
            p=bid
            qty=1.0/p if p>EPS else math.inf
            if (not math.isfinite(qty)) or qty<=EPS or qty>12.0+EPS:
                continue
            before=self.n
            self.submit(int(t),side,float(p),float(qty))
            key=f'{side}_{before}'
            reopened=any(x.get('slotId')==sid and x.get('event')=='SLOT_SUBMIT' for x in self.slot_history)
            self.slot_key[sid]=key
            self.slot_submits+=1
            if reopened:self.slot_reopens+=1
            self.slot_history.append({'t':int(t),'event':'SLOT_SUBMIT','slotId':sid,'key':key,'side':side,'price':p,'qty':qty})

    def run_probe(self,winner:str):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']); base.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        first_action_t=None; last_action_t=None
        for u in updates:
            t=int(u[1]); base.ex.advance_to(self.bt,t)
            self.process(t); self.cancel_expired(t); self._refresh_slots(t)
            base.apply(self.book,u); qv=base.quotes(self.book)
            if not qv: continue
            pre=self.slot_submits
            self._open_free_slots(t,qv,end)
            if self.slot_submits>pre:
                if first_action_t is None:first_action_t=t
                last_action_t=t
        end2=int(self.meta['lastReceivedMs']); base.ex.advance_to(self.bt,end2); self.process(end2); self._refresh_slots(end2)
        win=str(winner).upper(); pnl=float(self.inv.get(win,0.0)-self.cost)
        floor=float(min(self.inv.values())-self.cost); best=float(max(self.inv.values())-self.cost)
        active_window_s=max(0.0,(end-first)/1000.0-180.0)
        return {
            'maxSlots':self.max_slots,
            'submits':int(self.submits),
            'slotSubmits':int(self.slot_submits),
            'slotReopens':int(self.slot_reopens),
            'fillEvents':int(self.fills),
            'confirmedFillReceipts':int(self._fill_event_count),
            'filledQty':float(self.inv['UP']+self.inv['DOWN']),
            'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),
            'buyNotional':float(self.cost),'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
            'maxSimultaneousSlots':int(self.max_slots),
            'fullMarketSubmitsPerMin':float(self.submits/5.0),
            'fullMarketFillsPerMin':float(self.fills/5.0),
            'eligibleWindowSeconds':active_window_s,
            'eligibleWindowSubmitsPerMin':float(self.submits/(active_window_s/60.0)) if active_window_s>0 else None,
            'eligibleWindowFillsPerMin':float(self.fills/(active_window_s/60.0)) if active_window_s>0 else None,
            'firstActionT':first_action_t,'lastActionT':last_action_t,
            'slotFillEvents':self.slot_fill_events[:300],
            'slotHistory':self.slot_history[:1200],
        }

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='bare_slots_probe_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        if int(a.market_id) not in cohort: raise KeyError(f'market {a.market_id} not in bundle')
        cr=cohort[int(a.market_id)]; tape=tmp/'tapes'/f'{int(a.market_id)}.json.xz'
        rows=[]
        for slots in (1,4):
            sim=BareSlotProbe(tape,slots)
            try:r=sim.run_probe(cr['winner'])
            finally:sim.close()
            rows.append({'cell':f'BARE_{slots}SLOT',**r})
            print(json.dumps({'progress':f'BARE_{slots}SLOT','submits':r['submits'],'fills':r['fillEvents'],'filledQty':r['filledQty'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor']},ensure_ascii=False),flush=True)
        by={x['cell']:x for x in rows}; one=by['BARE_1SLOT']; four=by['BARE_4SLOT']
        out={
            'version':'ETH_BARE_EXECUTION_SLOTS_PROBE_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,
            'marketId':int(a.market_id),'winnerPostHocOnly':cr['winner'],
            'purpose':'Upper-bound diagnostic: can realistic HFT execution produce materially higher action/fill density when behavioral safety/admission policies are absent?',
            'rows':rows,
            'delta4Vs1':{
                'submits':four['submits']-one['submits'],'fills':four['fillEvents']-one['fillEvents'],'filledQty':four['filledQty']-one['filledQty'],
                'pnlDiagnosticOnly':four['pnlDiagnosticOnly']-one['pnlDiagnosticOnly'],'floor':four['floor']-one['floor']
            },
            'boundary':[
                'realistic HFT tape / risk queue / 250ms entry and response latency inherited from frozen primitive',
                'no Target action or winner used in runtime decisions; winner post-hoc PnL only',
                'no economic/recoverability/ownership/generation/payment-progress/passive-evidence/armed/churn admission gates',
                'public book imbalance supplies only a primitive direction; not a trained strategy',
                'one live carrier per slot; terminal carrier immediately frees slot; 5s inherited TTL',
                '1-slot and 4-slot use identical best-bid price/venue-min qty so only slot concurrency changes',
                '<=180s no-new-exposure project fence retained',
                'diagnostic execution-capacity probe only; not promotion evidence and not a valid standalone strategy',
                'no dream fill; no 8781'
            ]
        }
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'marketId':a.market_id,'rows':[{k:x[k] for k in ['cell','submits','fillEvents','filledQty','pnlDiagnosticOnly','floor','best','fullMarketSubmitsPerMin','fullMarketFillsPerMin','eligibleWindowSubmitsPerMin','eligibleWindowFillsPerMin']} for x in rows],'delta4Vs1':out['delta4Vs1']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
