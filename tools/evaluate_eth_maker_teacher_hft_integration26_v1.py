from __future__ import annotations
import argparse,json,lzma,math,statistics,tempfile,zipfile,shutil,sys
from collections import deque,defaultdict
from pathlib import Path
import numpy as np,joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
STAGING=ROOT/'.lan_worker_v1/staging'
if STAGING.exists() and str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import build_eth_target_teacher_policy_v1_dataset as base
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
EPS=1e-9
TTLS=(2000,5000,10000)

def live(s): return s in {'NEW','PARTIALLY_FILLED'}
def fill_price(side,snap,fallback):
    p=snap.get('execPrice')
    if p is None:return float(fallback)
    p=float(p);return p if side=='UP' else 1.0-p

def apply_archive_update(book,u):
    if int(u[3]):
        book['bids']={float(k):float(v) for k,v in (u[4] or {}).items()};book['asks']={float(k):float(v) for k,v in (u[5] or {}).items()}
        return {'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.,'levels':0.}
    ca={'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.,'levels':0.};ch=u[6] or {}
    for k in ('bids','asks'):
        for r in ch.get(k,[]) or []:
            p=float(r[0]);after=float(r[2]);d=float(r[3]);ca['levels']+=1
            if after<=EPS:book[k].pop(p,None)
            else:book[k][p]=after
            if d>=0:ca['bidadd' if k=='bids' else 'askadd']+=d
            else:ca['bidcut' if k=='bids' else 'askcut']+=-d
    return ca

def prior(hist,t,h,key):
    vals=[x for x in hist if x[0]<=t-h]
    if vals:return float(vals[-1][1].get(key,0.0))
    return float(hist[0][1].get(key,0.0)) if hist else 0.0

class Runner:
    def __init__(self,tape,model,ttl,entry=250,response=250,queue='risk'):
        self.payload=json.loads(lzma.decompress(tape.read_bytes()).decode('utf-8'));feed.ARCHIVE_DIR=tape.parent;self.events,self.times,self.meta=feed.build_archive_events(int(self.payload['marketId']),trade_offset='mid');self.bt=ex.new_bt(self.events,entry_latency_ms=entry,response_latency_ms=response,queue_model=queue);ex.initialize_bt(self.bt)
        self.m=model;self.ttl=ttl;self.orders={};self.nextid=1;self.inv={'UP':0.,'DOWN':0.};self.cost=0.;self.hist=deque();self.phist=[];self.shist=deque();self.chist=deque();self.book={'bids':{},'asks':{}};self.hazardSignals=0;self.submits=0;self.fills=0;self.makerFilled=0.;self.weakFilled=0.;self.domFilled=0.;self.trace=[]
    def close(self):self.bt.close()
    def snap(self,o):return ex.order_snapshot(self.bt,o['num'])
    def process(self,t):
        for k,o in list(self.orders.items()):
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0);inc=max(0.,cum-o['cum'])
            if inc>EPS:
                px=fill_price(o['side'],s,o['price']);self.inv[o['side']]+=inc;self.cost+=inc*px;self.fills+=1;self.makerFilled+=inc;self.hist.append((t,'MAKER',o['side'],inc,px));o['cum']=cum
                if o['weakAtSubmit']:self.weakFilled+=inc
                else:self.domFilled+=inc
            o['status']=s.get('status')
    def cancel_expired(self,t):
        for k,o in self.orders.items():
            s=self.snap(o)
            if live(s.get('status')) and t-o['placedMs']>=self.ttl:
                cur=self.bt.orders(0).get(o['num'])
                if cur is not None and bool(cur.cancellable):
                    try:self.bt.cancel(0,o['num'],False)
                    except Exception:pass
    def side_live(self,side):
        for o in self.orders.values():
            if o['side']==side and live(self.snap(o).get('status')):return True
        return False
    def submit(self,side,price,qty,t,weakflag):
        if self.side_live(side):return False
        n=self.nextid;self.nextid+=1;rc=ex.submit_native(self.bt,n,side,price,qty);self.orders[f'{side}_{n}']={'num':n,'side':side,'price':price,'qty':qty,'cum':0.,'placedMs':t,'status':'NEW','weakAtSubmit':weakflag};self.submits+=1;self.phist.append({'t':t,'side':side,'qty':qty});return True
    def features(self,t,u,ca):
        bf=base.book_features(self.book,int(u[2] or 0))
        if bf is None:return None,None
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta.get('lastReceivedMs') or t);bf['seconds_left']=(end-t)/1000.
        while self.hist and t-self.hist[0][0]>30000:self.hist.popleft()
        f,weak=base.state_features(self.inv['UP'],self.inv['DOWN'],self.cost,self.hist,t,len(self.hist),len(self.hist),bf)
        self.chist.append((t,ca.copy()));self.phist=[p for p in self.phist if t-p['t']<=30000]
        while self.chist and t-self.chist[0][0]>1500:self.chist.popleft()
        cur={'up_bid':bf['up_bid'],'up_ask':bf['up_ask'],'up_bid_depth':bf['up_bid_depth'],'up_ask_depth':bf['up_ask_depth'],'imbalance':bf['book_depth_imbalance']}
        f.update(update_add_qty=ca['bidadd']+ca['askadd'],update_cut_qty=ca['bidcut']+ca['askcut'],update_bid_add_qty=ca['bidadd'],update_ask_add_qty=ca['askadd'],update_bid_cut_qty=ca['bidcut'],update_ask_cut_qty=ca['askcut'],update_level_changes=float(ca['levels']))
        for h,nm in [(250,'250ms'),(1000,'1s')]:
            rr=[x for x in self.chist if t-x[0]<=h];f[f'updates_{nm}']=float(len(rr));f[f'add_qty_{nm}']=float(sum(x[1]['bidadd']+x[1]['askadd'] for x in rr));f[f'cut_qty_{nm}']=float(sum(x[1]['bidcut']+x[1]['askcut'] for x in rr));suffix='250' if h==250 else '1';f[f'up_bid_d{suffix}']=cur['up_bid']-prior(self.shist,t,h,'up_bid') if self.shist else 0.;f[f'up_ask_d{suffix}']=cur['up_ask']-prior(self.shist,t,h,'up_ask') if self.shist else 0.;f[f'up_bid_depth_d{suffix}']=cur['up_bid_depth']-prior(self.shist,t,h,'up_bid_depth') if self.shist else 0.;f[f'up_ask_depth_d{suffix}']=cur['up_ask_depth']-prior(self.shist,t,h,'up_ask_depth') if self.shist else 0.;f[f'imbalance_d{suffix}']=cur['imbalance']-prior(self.shist,t,h,'imbalance') if self.shist else 0.
        lp=self.phist[-1] if self.phist else None;f['last_placement_age_ms']=float(t-lp['t']) if lp else 1e6;f['last_placement_side_up']=1. if lp and lp['side']=='UP' else 0.;f['last_placement_qty']=float(lp['qty']) if lp else 0.;f['placement_events_2s']=float(sum(t-p['t']<=2000 for p in self.phist));f['placement_events_10s']=float(sum(t-p['t']<=10000 for p in self.phist));self.shist.append((t,cur));
        while self.shist and t-self.shist[0][0]>1500:self.shist.popleft()
        return f,weak
    def run(self,winner):
        updates=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);ex.advance_to(self.bt,first)
        feats=self.m['features'];th=self.m['thresholds']
        for u in updates:
            t=int(u[1]);ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=apply_archive_update(self.book,u);f,weak=self.features(t,u,ca)
            if f is None or float(f['seconds_left'])<=180:continue
            x=np.asarray([[float(f.get(k,0.) or 0.) for k in feats]],dtype=float);ph=float(self.m['hazardModel'].predict_proba(x)[0,1])
            if ph<float(th['hazard500ms']):continue
            self.hazardSignals+=1;ps=float(self.m['sideUpModel'].predict_proba(x)[0,1]);side='UP' if ps>=float(th['sideUp']) else 'DOWN';po=float(self.m['priceOffsetModel'].predict(x)[0]);pq=max(0.01,float(np.expm1(np.clip(self.m['qtyLowerBoundModel'].predict(x)[0],0,8))));bid=float(f['up_bid'] if side=='UP' else f['down_bid']);ask=float(f['up_ask'] if side=='UP' else f['down_ask']);price=round(bid+po*.01,2);price=max(.01,min(price,round(ask-.01,2),.99));
            if price<=0 or price>=ask-EPS:continue
            qty=max(pq,1.0/price);weakflag=bool(weak is not None and side==weak);self.submit(side,price,qty,t,weakflag)
        end=int(self.meta['lastReceivedMs']);ex.advance_to(self.bt,end);self.process(end)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=self.inv['UP']+self.inv['DOWN'];pc=2*min(self.inv.values())/gross if gross>EPS else 0.;floor=min(self.inv.values())-self.cost
        return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'pairCoverage':pc,'floor':floor,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'hazardSignals':self.hazardSignals,'submits':self.submits,'fillEvents':self.fills,'makerFilled':self.makerFilled,'weakFilled':self.weakFilled,'dominantFilled':self.domFilled}

def agg(rows):
    buys=sum(r['buyNotional'] for r in rows);p=sum(r['pnl'] for r in rows);active=[r for r in rows if r['buyNotional']>EPS]
    return {'markets':len(rows),'activeMarkets':len(active),'pnl':p,'buyNotional':buys,'roi':p/buys if buys else None,'winRateAll':sum(r['pnl']>0 for r in rows)/len(rows) if rows else None,'activeWinRate':sum(r['pnl']>0 for r in active)/len(active) if active else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rows) if rows else 0.,'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows) if rows else 0.,'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows) if rows else None,'meanAbsNet':statistics.mean(r['absNet'] for r in rows) if rows else 0.,'meanMakerFilled':statistics.mean(r['makerFilled'] for r in rows) if rows else 0.,'weakFillShare':sum(r['weakFilled'] for r in rows)/sum(r['weakFilled']+r['dominantFilled'] for r in rows) if sum(r['weakFilled']+r['dominantFilled'] for r in rows)>0 else None,'meanHazardSignals':statistics.mean(r['hazardSignals'] for r in rows) if rows else 0.,'meanSubmits':statistics.mean(r['submits'] for r in rows) if rows else 0.}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_maker_teacher_hft_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];model=joblib.load(a.model);allrows=[];total=len(cohort)*len(TTLS);n=0
        for ttl in TTLS:
            for cr in cohort:
                n+=1;mid=int(cr['marketId']);r=Runner(tmp/'tapes'/f'{mid}.json.xz',model,ttl)
                try:rr=r.run(cr['winner'])
                finally:r.close()
                rr.update({'marketId':mid,'ttlMs':ttl,'cohortTag':cr.get('cohortTag'),'winner':cr['winner'],'targetPnl':cr.get('targetPnl'),'targetBuy':cr.get('targetBuy')});allrows.append(rr)
                if n%10==0:print(json.dumps({'progress':n,'total':total,'ttl':ttl,'marketId':mid}),flush=True)
        summaries=[]
        for ttl in TTLS:
            for tag in ('DEV20','FRESH6','ALL'):
                rs=[r for r in allrows if r['ttlMs']==ttl and (tag=='ALL' or r['cohortTag']==tag)];s=agg(rs);s.update({'ttlMs':ttl,'cohortTag':tag});summaries.append(s)
        target=[]
        for tag in ('DEV20','FRESH6','ALL'):
            rs=[r for r in cohort if tag=='ALL' or r.get('cohortTag')==tag];buy=sum(float(r.get('targetBuy') or 0) for r in rs);p=sum(float(r.get('targetPnl') or 0) for r in rs);target.append({'cohortTag':tag,'markets':len(rs),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(float(r.get('targetPnl') or 0)>0 for r in rs)/len(rs) if rs else None})
        out={'version':'ETH_MAKER_TEACHER_HFT_INTEGRATION26_V1','boundary':['development integration only; all 26 markets excluded from placement-teacher training but outcomes have been seen in prior research','Maker-only: no Taker repair or ADD','model controls placement hazard, side, price offset, quantity lower-bound','execution-only constraints: post-only, 1 USDT minimum, <=180s no new exposure, one live order per side, preregistered TTL 2s/5s/10s','Target winner/PnL used only after replay for scoring'],'target':target,'summaries':summaries,'rows':allrows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'target':target,'summaries':summaries},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
