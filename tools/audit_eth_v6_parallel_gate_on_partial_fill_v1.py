from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_dagger60_smoke_v1 as v1

EPS=1e-9
MIDS=[1816356,1816646,1816800,1817999,1818835,1818992,1821664,1823488]
# side with known natural partial fill at qty=12 from reachability audit
PARTIAL_SIDE={1816356:'DOWN',1816646:'DOWN',1816800:'UP',1817999:'UP',1818835:'DOWN',1818992:'DOWN',1821664:'UP',1823488:'UP'}

class GateSim(v1.Sim):
    def __init__(self,tape,partial_side,ttl_ms=12000):
        super().__init__(tape,None,'FORCE_UP')
        self.partial_side=partial_side;self.ttl_ms=ttl_ms;self.first_valid=None
        self.repair_key=None;self.partial_seen=False;self.partial_rec=None
        self.expand_attempted=False;self.expand_blocked_by_outstanding=False;self.expand_allowed_if_gate_removed=False
        self.expand_side='DOWN' if partial_side=='UP' else 'UP'
    def cancel_expired(self,t):
        for o in self.orders.values():
            s=self.snap(o)
            if v1.live(s.get('status')) and t-o['placed']>=self.ttl_ms:
                cur=self.bt.orders(0).get(o['n'])
                if cur is not None and bool(cur.cancellable):
                    try:self.bt.cancel(0,o['n'],False)
                    except Exception:pass
    def process(self,t):
        for key,o in self.orders.items():
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0.0);inc=max(0.,cum-o['cum']);st=s.get('status')
            if inc>EPS:
                p=v1.fill_price(o['side'],s,o['price']);self.record_fill(t,o['side'],inc,p);self.fills+=1;o['cum']=cum
                if key==self.repair_key and (st=='PARTIALLY_FILLED' or (cum>EPS and cum<float(o['qty'])-EPS)) and not self.partial_seen:
                    self.partial_seen=True;self.partial_rec={'t':int(t),'inc':float(inc),'cum':float(cum),'qty':float(o['qty']),'leaves':float(s.get('leavesQty') or max(0.,o['qty']-cum)),'status':st}
            o['status']=st
    def outstanding_total(self):
        z=0.0
        for o in self.orders.values():
            s=self.snap(o);st=s.get('status')
            if st in {'NEW','PARTIALLY_FILLED'} or st is None:
                z+=max(0.0,float(o['qty'])-float(o.get('cum') or 0.0))
        return z
    def run(self):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        submitted=False
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv: continue
            if self.first_valid is None:self.first_valid=t
            if not submitted and t-self.first_valid>=2000 and (end-t)/1000>180:
                p=float(qv[self.partial_side]['bid']);q=max(12.0,1/p);self.submit(t,self.partial_side,p,q);self.repair_key=f'{self.partial_side}_{self.n-1}';submitted=True
            if self.partial_seen and not self.expand_attempted:
                self.expand_attempted=True
                out=self.outstanding_total()
                self.expand_blocked_by_outstanding=out>EPS
                self.expand_allowed_if_gate_removed=True
                break
        # no fabricated expand order is submitted; this audit isolates the V6 handoff gate at the exact natural partial-fill moment
        return {'marketId':int(self.payload['marketId']),'partialSide':self.partial_side,'partialSeen':self.partial_seen,'partial':self.partial_rec,'outstandingAtPartial':self.outstanding_total() if self.partial_seen else None,'expandAttempted':self.expand_attempted,'v6WouldBlockChildSwitch':self.expand_blocked_by_outstanding,'structurallyEligibleWithoutGlobalOutstandingGate':self.expand_allowed_if_gate_removed}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v6_gate_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);rows=[]
        for mid in MIDS:
            tp=tmp/'tapes'/f'{mid}.json.xz'
            if not tp.exists(): continue
            sim=GateSim(tp,PARTIAL_SIDE[mid])
            try:r=sim.run()
            finally:sim.close()
            rows.append(r);print(json.dumps(r),flush=True)
        partial=[r for r in rows if r['partialSeen']];blocked=[r for r in partial if r['v6WouldBlockChildSwitch']]
        out={'version':'ETH_V6_PARALLEL_GATE_ON_PARTIAL_FILL_V1','researchOnly':True,'rows':rows,'partialMarketsReproduced':len(partial),'blockedAtNaturalPartial':len(blocked),'allNaturalPartialWouldBeBlocked':bool(partial) and len(blocked)==len(partial),'interpretation':'If true, V6 learns parallel responsibility capabilities but execution child handoff remains serialized by the global outstanding-carrier gate.','boundary':['actual HftBacktest fills only','no dream/synthetic fills','does not submit a fabricated EXPAND order; audits the exact V6 outstanding_total child-switch gate at natural partial-fill moments']}
        op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'partialMarketsReproduced':len(partial),'blockedAtNaturalPartial':len(blocked),'allBlocked':out['allNaturalPartialWouldBeBlocked']}),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
