from __future__ import annotations
import argparse, json, math, statistics, tempfile, zipfile, shutil, sys
from collections import defaultdict
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed

POLL_MS=250
DECISION_MS=1000
MAKER_REPRICE_MS=5000
TAKER_COOLDOWN_MS=10000
EPS=1e-9

VARIANTS={
 'ETH_REPAIR_ONLY': dict(makerQty=10.0,makerMinPrice=0.10,weakOnlyGap=2.0,expandGate=2.0,addEnabled=False,takerRepair=True,takerGap=6.0,takerFrac=0.50,takerMaxAsk=0.25,tailMakerMin=0.10),
 'ETH_TARGET_STYLE': dict(makerQty=10.0,makerMinPrice=0.10,weakOnlyGap=2.0,expandGate=0.87,addEnabled=True,takerRepair=True,takerGap=6.0,takerFrac=0.50,takerMaxAsk=0.25,tailMakerMin=0.10),
 'BTC_LOOSE_STYLE': dict(makerQty=18.0,makerMinPrice=0.04,weakOnlyGap=18.0,expandGate=0.75,addEnabled=True,takerRepair=True,takerGap=18.0,takerFrac=0.25,takerMaxAsk=0.35,tailMakerMin=0.04),
}

def book(bt):
    d=bt.depth(0); a=d.snapshot()
    try:
        bids={}; asks={}
        for r in a:
            q=float(r['qty']); p=round(float(r['px']),12); ev=int(r['ev'])
            if q<=EPS: continue
            if ev & int(ex.BUY_EVENT): bids[p]=q
            elif ev & int(ex.SELL_EVENT): asks[p]=q
        return bids,asks
    finally: d.snapshot_free(a)

def outcome_quotes(bt):
    bids,asks=book(bt)
    if not bids or not asks: return None
    nb=max(bids); na=min(asks)
    up_bid=nb; up_ask=na
    down_bid=1.0-na; down_ask=1.0-nb
    if not (0<up_bid<1 and 0<up_ask<1 and 0<down_bid<1 and 0<down_ask<1): return None
    return {'UP':{'bid':up_bid,'ask':up_ask},'DOWN':{'bid':down_bid,'ask':down_ask},'upMid':(up_bid+up_ask)/2}

def fill_price(side,snap,fallback):
    p=snap.get('execPrice')
    if p is None: return float(fallback)
    p=float(p); return p if side=='UP' else 1.0-p

def live_status(s): return s in {'NEW','PARTIALLY_FILLED'}

class Runner:
    def __init__(self,tape_path,variant,entry=250,response=250,queue='risk'):
        feed.ARCHIVE_DIR=tape_path.parent
        self.events,self.times,self.meta=feed.build_archive_events(int(tape_path.stem.split('.')[0]),trade_offset='mid')
        self.bt=ex.new_bt(self.events,entry_latency_ms=entry,response_latency_ms=response,queue_model=queue); ex.initialize_bt(self.bt)
        self.v=variant; self.orders={}; self.nextnum=1; self.inv={'UP':0.0,'DOWN':0.0}; self.cost=0.0; self.fillCount=0; self.makerFill=0.0; self.takerFill=0.0; self.addFill=0.0; self.lastTakerMs=None; self.midHist=[]; self.trace=[]
    def close(self): self.bt.close()
    def snap(self,o): return ex.order_snapshot(self.bt,o['num'])
    def process(self,now):
        for key,o in list(self.orders.items()):
            s=self.snap(o); cum=float(s.get('cumExecQty') or 0.0); inc=max(0.0,cum-o['cum'])
            if inc>EPS:
                px=fill_price(o['side'],s,o['price']); self.inv[o['side']]+=inc; self.cost+=inc*px; self.fillCount+=1
                if o['channel']=='MAKER': self.makerFill+=inc
                elif o['channel']=='TAKER': self.takerFill+=inc
                elif o['channel']=='ADD': self.addFill+=inc
                o['cum']=cum; self.trace.append({'atMs':now,'type':'FILL','key':key,'channel':o['channel'],'side':o['side'],'qty':inc,'price':px,'up':self.inv['UP'],'down':self.inv['DOWN']})
            o['status']=s.get('status')
            if not live_status(o['status']) and o['status'] not in {'NONE'}:
                o['terminalMs']=now
    def cancel(self,key,now):
        o=self.orders.get(key)
        if not o: return
        s=self.snap(o)
        if live_status(s.get('status')):
            cur=self.bt.orders(0).get(o['num'])
            if cur is not None and bool(cur.cancellable):
                try: self.bt.cancel(0,o['num'],False); o['cancelReqMs']=now
                except Exception: pass
    def submit(self,key,side,price,qty,channel,now,gtc=False):
        old=self.orders.get(key)
        if old and live_status(self.snap(old).get('status')): return False
        num=self.nextnum; self.nextnum+=1
        native_side,native_price=ex.native_order(side,price)
        if gtc:
            if native_side=='BUY': rc=int(self.bt.submit_buy_order(0,num,native_price,float(qty),ex.hbt.GTC,ex.LIMIT,False))
            else: rc=int(self.bt.submit_sell_order(0,num,native_price,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        else: rc=ex.submit_native(self.bt,num,side,price,qty)
        self.orders[key]={'num':num,'side':side,'price':float(price),'qty':float(qty),'channel':channel,'cum':0.0,'placedMs':now,'rc':rc,'status':'NEW'}
        self.trace.append({'atMs':now,'type':'SUBMIT','key':key,'channel':channel,'side':side,'price':price,'qty':qty,'rc':rc}); return True
    def paircov(self):
        u,d=self.inv['UP'],self.inv['DOWN']; tot=u+d
        return 2*min(u,d)/tot if tot>EPS else 0.0
    def floor(self): return min(self.inv['UP'],self.inv['DOWN'])-self.cost
    def momentum(self,now,mid):
        self.midHist.append((now,mid)); self.midHist=[x for x in self.midHist if now-x[0]<=7000]
        old=min(self.midHist,key=lambda x:abs((now-5000)-x[0])) if self.midHist else (now,mid)
        return mid-old[1]
    def desired_maker(self,q,now,seconds_left):
        u,d=self.inv['UP'],self.inv['DOWN']; total=u+d; gap=abs(u-d); weak='UP' if u<d else 'DOWN' if d<u else None
        desired={}
        if total<=EPS:
            if seconds_left>180 and q['UP']['bid']+q['DOWN']['bid']<=0.99+1e-9:
                desired={'BASE_UP':('UP',q['UP']['bid'],self.v['makerQty']),'BASE_DOWN':('DOWN',q['DOWN']['bid'],self.v['makerQty'])}
            return desired
        if gap>=self.v['weakOnlyGap'] and weak:
            p=q[weak]['bid']
            if p>=self.v['tailMakerMin'] and p*self.v['makerQty']>=1-1e-9: desired[f'WEAK_{weak}']=(weak,p,self.v['makerQty'])
        elif seconds_left>180:
            for side in ('UP','DOWN'):
                p=q[side]['bid']
                if p>=self.v['makerMinPrice'] and p*self.v['makerQty']>=1-1e-9: desired[f'BASE_{side}']=(side,p,self.v['makerQty'])
        return desired
    def manage_makers(self,desired,now):
        maker_keys=[k for k,o in self.orders.items() if o['channel']=='MAKER']
        for k in maker_keys:
            o=self.orders[k]
            if k not in desired: self.cancel(k,now); continue
            _,p,_=desired[k]
            if abs(float(o['price'])-float(p))>0.005 and now-o['placedMs']>=MAKER_REPRICE_MS: self.cancel(k,now)
        for k,(side,p,qty) in desired.items():
            o=self.orders.get(k)
            if o and live_status(self.snap(o).get('status')): continue
            self.submit(k,side,p,qty,'MAKER',now,False)
    def maybe_taker_repair(self,q,now):
        if not self.v['takerRepair']: return
        if self.lastTakerMs is not None and now-self.lastTakerMs<TAKER_COOLDOWN_MS: return
        u,d=self.inv['UP'],self.inv['DOWN']; gap=abs(u-d)
        if gap<self.v['takerGap']: return
        weak='UP' if u<d else 'DOWN'; ask=q[weak]['ask']
        if ask<=0 or ask>self.v['takerMaxAsk']: return
        qty=max(1.0/0.95, gap*self.v['takerFrac']); qty=min(qty,gap)
        if qty<=EPS: return
        max_price=max(ask,min(0.95,1.0/qty+0.01))
        key=f'TAKER_{now}'
        if self.submit(key,weak,max_price,qty,'TAKER',now,True): self.lastTakerMs=now
    def maybe_add(self,q,now,seconds_left,mom):
        key='ADD'
        if not self.v['addEnabled'] or seconds_left<=180 or self.paircov()<self.v['expandGate'] or abs(mom)<0.02:
            self.cancel(key,now); return
        side='UP' if mom>0 else 'DOWN'; p=q[side]['bid']; qty=self.v['makerQty']
        if p<self.v['makerMinPrice'] or p*qty<1-1e-9: self.cancel(key,now); return
        o=self.orders.get(key)
        if o and live_status(self.snap(o).get('status')):
            if o['side']!=side or (abs(o['price']-p)>0.005 and now-o['placedMs']>=MAKER_REPRICE_MS): self.cancel(key,now)
            return
        self.submit(key,side,p,qty,'ADD',now,False)
    def run(self,winner):
        first=int(self.meta['firstReceivedMs']); last=int(self.meta['lastReceivedMs']); now=first+2000; end=last
        ex.advance_to(self.bt,now); self.process(now)
        next_dec=now
        while now<end:
            now=min(end,now+POLL_MS); ex.advance_to(self.bt,now); self.process(now)
            if now<next_dec: continue
            next_dec=now+DECISION_MS
            q=outcome_quotes(self.bt)
            if not q: continue
            seconds_left=max(0.0,(last-now)/1000.0); mom=self.momentum(now,q['upMid'])
            desired=self.desired_maker(q,now,seconds_left); self.manage_makers(desired,now); self.maybe_taker_repair(q,now); self.maybe_add(q,now,seconds_left,mom)
        for k in list(self.orders): self.cancel(k,end)
        ex.advance_to(self.bt,end); self.process(end)
        pnl=(self.inv[winner]-self.cost) if winner in {'UP','DOWN'} else -self.cost
        return {'pnl':pnl,'buyNotional':self.cost,'up':self.inv['UP'],'down':self.inv['DOWN'],'pairCoverage':self.paircov(),'floor':self.floor(),'fillCount':self.fillCount,'makerFilled':self.makerFill,'takerFilled':self.takerFill,'addFilled':self.addFill,'orders':len(self.orders),'traceEvents':len(self.trace)}

def agg(rows):
    ps=[r['pnl'] for r in rows]; buys=sum(r['buyNotional'] for r in rows); return {'markets':len(rows),'pnl':sum(ps),'buyNotional':buys,'roi':sum(ps)/buys if buys else None,'winRate':sum(x>0 for x in ps)/len(ps) if ps else None,'meanPnl':statistics.mean(ps) if ps else None,'medianPnl':statistics.median(ps) if ps else None,'maxWin':max(ps) if ps else None,'maxLoss':min(ps) if ps else None,'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'positiveFloorRate':sum(r['floor']>=0 for r in rows)/len(rows),'meanAbsNet':statistics.mean(abs(r['up']-r['down']) for r in rows),'meanBuy':statistics.mean(r['buyNotional'] for r in rows),'meanAddFilled':statistics.mean(r['addFilled'] for r in rows),'meanTakerFilled':statistics.mean(r['takerFilled'] for r in rows)}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='eth_style_run_'))
    try:
        with zipfile.ZipFile(a.bundle) as z: z.extractall(tmp)
        cohort=json.loads((tmp/'cohort.json').read_text())['rows']; allrows=[]
        settings=[(250,250,'risk'),(750,250,'risk'),(250,250,'log')]
        total=len(cohort)*len(VARIANTS)*len(settings); n=0
        for entry,response,queue in settings:
            for name,v in VARIANTS.items():
                for cr in cohort:
                    n+=1; mid=int(cr['marketId']); runner=Runner(tmp/'tapes'/f'{mid}.json.xz',v,entry,response,queue)
                    try: rr=runner.run(str(cr['winner']).upper())
                    finally: runner.close()
                    rr.update({'marketId':mid,'variant':name,'entryLatencyMs':entry,'responseLatencyMs':response,'queueModel':queue,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']}); allrows.append(rr)
                    if n%10==0: print(json.dumps({'progress':n,'total':total,'marketId':mid,'variant':name}),flush=True)
        summaries=[]
        for entry,response,queue in settings:
            for name in VARIANTS:
                rr=[r for r in allrows if r['variant']==name and r['entryLatencyMs']==entry and r['responseLatencyMs']==response and r['queueModel']==queue]; s=agg(rr); s.update({'variant':name,'entryLatencyMs':entry,'responseLatencyMs':response,'queueModel':queue}); summaries.append(s)
        target={'markets':len(cohort),'pnl':sum(float(r['targetPnl']) for r in cohort),'buyNotional':sum(float(r['targetBuy']) for r in cohort)}; target['roi']=target['pnl']/target['buyNotional'] if target['buyNotional'] else None; target['winRate']=sum(float(r['targetPnl'])>0 for r in cohort)/len(cohort)
        out={'version':'ETH_TARGET_STYLE_HFT_DEV20_V1','boundary':['development-only; structural rules were informed by earlier Target ETH anatomy and are not fresh holdout evidence','Target winner is used only after simulation for settlement scoring; Target orders/actions/PnL never enter decisions','no new balanced base or ADD when <=180s remain; weak-side repair may continue','Taker has no fixed share cap; repair quantity is proportional to current gap'],'cohort':[r['marketId'] for r in cohort],'targetSameCohort':target,'summaries':summaries,'rows':allrows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'output':a.output,'target':target,'summaries':summaries},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
