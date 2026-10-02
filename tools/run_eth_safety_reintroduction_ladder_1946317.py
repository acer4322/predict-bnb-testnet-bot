from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path: sys.path.insert(0,str(HBT244))

import tools.run_eth_dagger60_smoke_v1 as base

EPS=1e-9
MID=1946317
NO_NEW_EXPOSURE_MS=180000
CELLS=[
    ('B0_BARE',False,False,False),
    ('B1_PAIR_ECONOMICS',True,False,False),
    ('B2_FLOOR_NONWORSE',False,True,False),
    ('B3_SAME_SIDE_SERIALIZATION',False,False,True),
    ('B4_PAIR_PLUS_FLOOR',True,True,False),
    ('B5_PAIR_PLUS_SERIALIZATION',True,False,True),
    ('B6_FLOOR_PLUS_SERIALIZATION',False,True,True),
    ('B7_ALL_THREE',True,True,True),
]

class LadderSim(base.Sim):
    def __init__(self,tape,max_slots:int,pair_guard:bool,floor_guard:bool,serialize_guard:bool):
        super().__init__(tape,traj=None,seed='FORCE_UP')
        self.max_slots=int(max_slots)
        self.pair_guard=bool(pair_guard); self.floor_guard=bool(floor_guard); self.serialize_guard=bool(serialize_guard)
        self.slot_key={}; self.slot_history=[]; self.slot_reopens=0; self.veto=Counter(); self.fill_receipts=0

    def record_fill(self,t,side,q,p):
        super().record_fill(t,side,q,p); self.fill_receipts+=1

    def _refresh_slots(self,t:int):
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if not o:
                self.slot_key.pop(sid,None); continue
            try:s=self.snap(o)
            except Exception:s={}
            if not base.live(s.get('status')):
                self.slot_history.append({'t':int(t),'event':'SLOT_RELEASE','slotId':sid,'key':key,'status':s.get('status'),'cum':float(s.get('cumExecQty') or o.get('cum') or 0.0)})
                self.slot_key.pop(sid,None)

    def _direction(self,qv):
        return 'UP' if float(qv.get('imb') or 0.0)>=0 else 'DOWN'

    def _same_side_live(self,side):
        # slot_key is the local authoritative reservation set: it includes submit->venue latency
        # and remains occupied until _refresh_slots observes terminal state.
        # This deliberately tests serialization including pending/in-flight authority.
        for key in self.slot_key.values():
            o=self.orders.get(key)
            if o and o.get('side')==side:
                return True
        return False

    def _pair_ok(self,side,p):
        opp='DOWN' if side=='UP' else 'UP'
        oppq=sum(float(a) for a,_ in self.un[opp])
        if oppq<=EPS:return True
        avg=self.unmatched_avg(opp)
        return avg is not None and float(avg)+float(p)<=1.0000001

    def _floor_ok(self,side,p,q):
        before=min(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost)
        up=float(self.inv['UP'])+(float(q) if side=='UP' else 0.0)
        dn=float(self.inv['DOWN'])+(float(q) if side=='DOWN' else 0.0)
        after=min(up,dn)-(float(self.cost)+float(q)*float(p))
        return after>=before-1e-9

    def _open_free_slots(self,t,qv,end):
        if int(end)-int(t)<=NO_NEW_EXPOSURE_MS:
            self.veto['LATE_180S']+=1; return
        side=self._direction(qv); bid=float(qv[side]['bid']); ask=float(qv[side]['ask'])
        if bid<=EPS or bid>=1.0-EPS or ask<=bid+EPS:
            self.veto['PHYSICAL_PRICE_INVALID']+=1; return
        qty=1.0/bid if bid>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:
            self.veto['VENUE_MIN_INFEASIBLE']+=1; return
        for sid in range(1,self.max_slots+1):
            if sid in self.slot_key: continue
            if self.serialize_guard and self._same_side_live(side):
                self.veto['SAME_SIDE_SERIALIZATION']+=1; continue
            if self.pair_guard and not self._pair_ok(side,bid):
                self.veto['PAIR_ECONOMICS']+=1; continue
            if self.floor_guard and not self._floor_ok(side,bid,qty):
                self.veto['FLOOR_NONWORSE']+=1; continue
            before=self.n; self.submit(int(t),side,bid,qty); key=f'{side}_{before}'
            reopened=any(x.get('slotId')==sid and x.get('event')=='SLOT_SUBMIT' for x in self.slot_history)
            if reopened:self.slot_reopens+=1
            self.slot_key[sid]=key
            self.slot_history.append({'t':int(t),'event':'SLOT_SUBMIT','slotId':sid,'key':key,'side':side,'price':bid,'qty':qty})

    def run_ladder(self,winner):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']); base.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in updates:
            t=int(u[1]); base.ex.advance_to(self.bt,t); self.process(t); self.cancel_expired(t); self._refresh_slots(t)
            base.apply(self.book,u); qv=base.quotes(self.book)
            if qv:self._open_free_slots(t,qv,end)
        end2=int(self.meta['lastReceivedMs']); base.ex.advance_to(self.bt,end2); self.process(end2); self._refresh_slots(end2)
        win=str(winner).upper(); pnl=float(self.inv.get(win,0.0)-self.cost); floor=float(min(self.inv.values())-self.cost); best=float(max(self.inv.values())-self.cost)
        return {
            'submits':int(self.submits),'fillEvents':int(self.fills),'confirmedFillReceipts':int(self.fill_receipts),
            'filledQty':float(self.inv['UP']+self.inv['DOWN']),'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),
            'buyNotional':float(self.cost),'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
            'slotReopens':int(self.slot_reopens),'vetoCounts':dict(self.veto),
            'fullMarketSubmitsPerMin':float(self.submits/5.0),'fullMarketFillsPerMin':float(self.fills/5.0),
        }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=MID: raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='safety_reintro_1946317_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; cr=cohort[MID]; tape=tmp/'tapes'/f'{MID}.json.xz'
        rows=[]
        for label,pair,floor,serial in CELLS:
            for slots in (1,4):
                sim=LadderSim(tape,slots,pair,floor,serial)
                try:r=sim.run_ladder(cr['winner'])
                finally:sim.close()
                row={'cell':label,'maxSlots':slots,'pairEconomics':pair,'floorNonworse':floor,'sameSideSerialization':serial,**r}; rows.append(row)
                print(json.dumps({'progress':label,'slots':slots,'submits':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'veto':r['vetoCounts']},ensure_ascii=False),flush=True)
        baseline={(x['maxSlots']):x for x in rows if x['cell']=='B0_BARE'}
        for row in rows:
            b=baseline[row['maxSlots']]
            row['submitRetentionVsBare']=float(row['submits']/b['submits']) if b['submits'] else None
            row['fillRetentionVsBare']=float(row['fillEvents']/b['fillEvents']) if b['fillEvents'] else None
            row['pnlDeltaVsBare']=float(row['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'])
            row['floorDeltaVsBare']=float(row['floor']-b['floor'])
        out={'version':'ETH_SAFETY_REINTRODUCTION_LADDER_1946317_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'rows':rows,'boundary':['first reverse-ablation ladder on bare execution substrate','PAIR_ECONOMICS exact pair-cost rule on existing unmatched opposite inventory','FLOOR_NONWORSE exact hypothetical full-fill floor non-worsening veto','SAME_SIDE_SERIALIZATION isolates liveness cost of one-live-same-side authority','does not yet model responsibility-aware payment-progress/passive-evidence/recoverability/generation/role guards','same realistic HFT tape, risk queue, 250ms entry/response latency, 5s TTL, <=180s fence','no Target runtime input','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'rows':[{'cell':x['cell'],'slots':x['maxSlots'],'submits':x['submits'],'fills':x['fillEvents'],'submitRetention':x['submitRetentionVsBare'],'fillRetention':x['fillRetentionVsBare'],'pnl':x['pnlDiagnosticOnly'],'floor':x['floor'],'veto':x['vetoCounts']} for x in rows]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
