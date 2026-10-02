from __future__ import annotations
import argparse,json,statistics,tempfile,zipfile,shutil,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import evaluate_eth_target_style_hft_dev20_v1 as base

V2_VARIANTS={
 'ETH_ASYM_PAIR_NOADD': dict(makerQty=10.0,makerMinPrice=0.10,weakOnlyGap=10.0,expandGate=2.0,addEnabled=False,takerRepair=True,takerGap=10.0,takerFrac=0.50,takerMaxAsk=0.25,tailMakerMin=0.10,basePairMax=0.98,repairPairMax=1.00),
 'ETH_ASYM_PAIR_ADD': dict(makerQty=10.0,makerMinPrice=0.10,weakOnlyGap=10.0,expandGate=0.87,addEnabled=True,takerRepair=True,takerGap=10.0,takerFrac=0.50,takerMaxAsk=0.25,tailMakerMin=0.10,basePairMax=0.98,repairPairMax=1.00),
}

class RunnerV2(base.Runner):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw); self.unmatched={'UP':[],'DOWN':[]}; self.pairSums=[]
    def _match_lot(self,side,qty,price):
        opp='DOWN' if side=='UP' else 'UP'; rem=float(qty); lots=self.unmatched[opp]
        while rem>base.EPS and lots:
            lq,lp=lots[0]; m=min(rem,lq); self.pairSums.append((float(lp)+float(price),m)); rem-=m; lq-=m
            if lq<=base.EPS: lots.pop(0)
            else: lots[0]=[lq,lp]
        if rem>base.EPS: self.unmatched[side].append([rem,float(price)])
    def process(self,now):
        for key,o in list(self.orders.items()):
            s=self.snap(o); cum=float(s.get('cumExecQty') or 0.0); inc=max(0.0,cum-o['cum'])
            if inc>base.EPS:
                px=base.fill_price(o['side'],s,o['price']); self._match_lot(o['side'],inc,px); self.inv[o['side']]+=inc; self.cost+=inc*px; self.fillCount+=1
                if o['channel']=='MAKER': self.makerFill+=inc
                elif o['channel']=='TAKER': self.takerFill+=inc
                elif o['channel']=='ADD': self.addFill+=inc
                o['cum']=cum; self.trace.append({'atMs':now,'type':'FILL','key':key,'channel':o['channel'],'side':o['side'],'qty':inc,'price':px,'up':self.inv['UP'],'down':self.inv['DOWN']})
            o['status']=s.get('status')
            if not base.live_status(o['status']) and o['status'] not in {'NONE'}: o['terminalMs']=now
    def candidate_pair_sum(self,side,price,qty=None):
        opp='DOWN' if side=='UP' else 'UP'; lots=self.unmatched[opp]
        if not lots: return None
        need=float(qty if qty is not None else sum(x[0] for x in lots)); take=0.0; val=0.0
        for q,p in lots:
            m=min(q,max(0.0,need-take)); val+=m*p; take+=m
            if take>=need-base.EPS: break
        if take<=base.EPS: return None
        return val/take + float(price)
    def desired_maker(self,q,now,seconds_left):
        u,d=self.inv['UP'],self.inv['DOWN']; total=u+d; gap=abs(u-d); weak='UP' if u<d else 'DOWN' if d<u else None; desired={}
        if total<=base.EPS:
            if seconds_left>180 and q['UP']['bid']+q['DOWN']['bid']<=self.v['basePairMax']+1e-9:
                desired={'BASE_UP':('UP',q['UP']['bid'],self.v['makerQty']),'BASE_DOWN':('DOWN',q['DOWN']['bid'],self.v['makerQty'])}
            return desired
        if gap>=self.v['weakOnlyGap'] and weak:
            p=q[weak]['bid']; ps=self.candidate_pair_sum(weak,p,self.v['makerQty'])
            if p>=self.v['tailMakerMin'] and p*self.v['makerQty']>=1-1e-9 and ps is not None and ps<=self.v['repairPairMax']+1e-9:
                desired[f'WEAK_{weak}']=(weak,p,self.v['makerQty'])
        elif seconds_left>180 and q['UP']['bid']+q['DOWN']['bid']<=self.v['basePairMax']+1e-9:
            for side in ('UP','DOWN'):
                p=q[side]['bid']
                if p>=self.v['makerMinPrice'] and p*self.v['makerQty']>=1-1e-9: desired[f'BASE_{side}']=(side,p,self.v['makerQty'])
        return desired
    def maybe_taker_repair(self,q,now):
        if self.lastTakerMs is not None and now-self.lastTakerMs<base.TAKER_COOLDOWN_MS: return
        u,d=self.inv['UP'],self.inv['DOWN']; gap=abs(u-d)
        if gap<self.v['takerGap']: return
        weak='UP' if u<d else 'DOWN'; ask=q[weak]['ask']
        if ask<=0 or ask>self.v['takerMaxAsk']: return
        qty=max(1.0/0.95,gap*self.v['takerFrac']); qty=min(qty,gap)
        ps=self.candidate_pair_sum(weak,ask,qty)
        if ps is None or ps>self.v['repairPairMax']+1e-9: return
        max_price=max(ask,min(0.95,1.0/qty+0.01)); key=f'TAKER_{now}'
        if self.submit(key,weak,max_price,qty,'TAKER',now,True): self.lastTakerMs=now
    def run(self,winner):
        r=super().run(winner); paired_qty=sum(q for _,q in self.pairSums); r['meanPairSum']=sum(ps*q for ps,q in self.pairSums)/paired_qty if paired_qty else None; r['pairedQtyTracked']=paired_qty; r['unmatchedQty']=sum(q for s in self.unmatched.values() for q,_ in s); return r

def agg(rows):
    s=base.agg(rows); vals=[r['meanPairSum'] for r in rows if r.get('meanPairSum') is not None]; s['meanOfMarketPairSum']=statistics.mean(vals) if vals else None; s['meanUnmatchedQty']=statistics.mean(r['unmatchedQty'] for r in rows); return s

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); tmp=Path(tempfile.mkdtemp(prefix='eth_style_v2_'))
    try:
        with zipfile.ZipFile(a.bundle) as z: z.extractall(tmp)
        cohort=json.loads((tmp/'cohort.json').read_text())['rows']; allrows=[]; settings=[(250,250,'risk'),(750,250,'risk'),(250,250,'log')]; total=len(cohort)*len(V2_VARIANTS)*len(settings); n=0
        for entry,response,queue in settings:
            for name,v in V2_VARIANTS.items():
                for cr in cohort:
                    n+=1; mid=int(cr['marketId']); rrn=RunnerV2(tmp/'tapes'/f'{mid}.json.xz',v,entry,response,queue)
                    try: rr=rrn.run(str(cr['winner']).upper())
                    finally: rrn.close()
                    rr.update({'marketId':mid,'variant':name,'entryLatencyMs':entry,'responseLatencyMs':response,'queueModel':queue,'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy']}); allrows.append(rr)
                    if n%10==0: print(json.dumps({'progress':n,'total':total,'variant':name,'marketId':mid}),flush=True)
        sums=[]
        for entry,response,queue in settings:
            for name in V2_VARIANTS:
                rr=[r for r in allrows if r['variant']==name and r['entryLatencyMs']==entry and r['queueModel']==queue]; x=agg(rr); x.update({'variant':name,'entryLatencyMs':entry,'responseLatencyMs':response,'queueModel':queue}); sums.append(x)
        target={'markets':len(cohort),'pnl':sum(float(r['targetPnl']) for r in cohort),'buyNotional':sum(float(r['targetBuy']) for r in cohort)}; target['roi']=target['pnl']/target['buyNotional']; target['winRate']=sum(float(r['targetPnl'])>0 for r in cohort)/len(cohort)
        out={'version':'ETH_TARGET_STYLE_HFT_DEV20_V2_ASYM_PAIR','boundary':['development-only same dev20 cohort','adds explicit ALLOW_ASYMMETRY gap=10 and marginal pair-sum gate <=1.00','base paired Maker quoting requires contemporaneous bid pair sum <=0.98','Target actions never enter decisions'],'targetSameCohort':target,'summaries':sums,'rows':allrows}; Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'target':target,'summaries':sums},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
