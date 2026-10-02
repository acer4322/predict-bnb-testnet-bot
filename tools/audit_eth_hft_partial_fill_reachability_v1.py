from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
import numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
    from tools import run_eth_dagger60_smoke_v1 as v1
except ImportError:
    import importlib.util
    p=Path(__file__).resolve().with_name('run_eth_dagger60_smoke_v1.py');s=importlib.util.spec_from_file_location('v1_partial',p);v1=importlib.util.module_from_spec(s);s.loader.exec_module(v1)

EPS=1e-9

class ReachSim(v1.Sim):
    def __init__(self,tape,side,qty,ttl_ms=12000):
        super().__init__(tape,None,'FORCE_UP');self.force_side=side;self.force_qty=float(qty);self.ttl_ms=int(ttl_ms);self.partial=[];self.fill_events=[];self.submitted_key=None
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
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0.0);inc=max(0.,cum-o['cum']);status=s.get('status')
            if inc>EPS:
                p=v1.fill_price(o['side'],s,o['price']);self.record_fill(t,o['side'],inc,p);self.fills+=1;o['cum']=cum
                rec={'t':int(t),'key':key,'side':o['side'],'inc':float(inc),'cum':float(cum),'qty':float(o['qty']),'status':status,'leavesQty':float(s.get('leavesQty') or max(0.,o['qty']-cum))}
                self.fill_events.append(rec)
                if status=='PARTIALLY_FILLED' or (cum>EPS and cum<float(o['qty'])-EPS):self.partial.append(rec)
            o['status']=status
    def run(self):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);first_valid=None;submitted=False
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if first_valid is None:first_valid=t
            if not submitted and t-first_valid>=2000 and (end-t)/1000>180:
                side=self.force_side;p=float(qv[side]['bid']);qty=max(self.force_qty,1/p);self.submit(t,side,p,qty);self.submitted_key=f'{side}_{self.n-1}';submitted=True
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        o=self.orders.get(self.submitted_key) if self.submitted_key else None
        final=None
        if o is not None:
            s=self.snap(o);final={'status':s.get('status'),'cum':float(s.get('cumExecQty') or 0.),'qty':float(o['qty']),'leaves':float(s.get('leavesQty') or 0.)}
        return {'submitted':submitted,'partialEvents':len(self.partial),'fillEvents':len(self.fill_events),'partial':self.partial[:5],'final':final,'buyNotional':float(self.cost)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output');ap.add_argument('--qtys',default='12,24,40');ap.add_argument('--max-markets',type=int,default=101);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_partial_reach_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];test=[r for r in cohort if r['split']!='TRAIN40'][:a.max_markets];qtys=[float(x) for x in a.qtys.split(',') if x.strip()];rows=[];hits=[]
        total=len(test)*2*len(qtys);n=0
        for cr in test:
            mid=int(cr['marketId']);tp=tmp/'tapes'/f'{mid}.json.xz'
            for side in ('UP','DOWN'):
                for qty in qtys:
                    n+=1;sim=ReachSim(tp,side,qty)
                    try:r=sim.run()
                    finally:sim.close()
                    r.update({'marketId':mid,'side':side,'qty':qty,'winner':cr.get('winner')});rows.append(r)
                    if r['partialEvents']>0:hits.append(r)
                    if n%40==0:print(json.dumps({'progress':n,'of':total,'partialHits':len(hits)}),flush=True)
        byqty={str(q):{'trials':0,'hits':0} for q in qtys}
        for r in rows:
            z=byqty[str(r['qty'])];z['trials']+=1;z['hits']+=int(r['partialEvents']>0)
        unique=sorted(set(int(r['marketId']) for r in hits));out={'version':'ETH_HFT_PARTIAL_FILL_REACHABILITY_V1','researchOnly':True,'trials':len(rows),'partialHitTrials':len(hits),'partialHitMarkets':len(unique),'partialMarketIds':unique,'byQty':byqty,'hits':hits,'boundary':['actual HftBacktest execution only','passive bid only','no dream/synthetic fills','larger qty/TTL are functional reachability stress only, not policy']}
        op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'trials':len(rows),'partialHitTrials':len(hits),'partialHitMarkets':len(unique),'marketIds':unique[:30],'byQty':byqty},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
