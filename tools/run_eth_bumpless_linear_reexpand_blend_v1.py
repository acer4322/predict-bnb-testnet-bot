from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

class BumplessBlendSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.expDebt=0.0
        self.peakDebt=0.0
        self.blendAttempts=0
        self.blendSubmits=0
        self.blendHeldBelowMin=0
        self.alphaHist=[]
        self.desiredQtyHist=[]
        self.sentQtyHist=[]

    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        delta=post-pre
        if delta>v1.EPS:
            self.expDebt=max(0.0,self.expDebt)+delta
            self.peakDebt=max(self.peakDebt,self.expDebt)
        elif delta<-v1.EPS and self.expDebt>v1.EPS:
            self.expDebt=max(0.0,self.expDebt-(-delta))
            if self.expDebt<=v1.EPS:
                self.expDebt=0.0
                self.peakDebt=0.0

    def repair_progress(self):
        if self.expDebt<=v1.EPS or self.peakDebt<=v1.EPS:return 1.0
        return max(0.0,min(1.0,(self.peakDebt-self.expDebt)/self.peakDebt))

    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+v1.EPS

    def run_student_blend(self,models,winner):
        ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
        first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first)
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        for u in ups:
            t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
            if not qv:continue
            if self.firstValid is None:self.firstValid=t
            if (end-t)/1000.<=180:continue
            self.seed_if_needed(t,qv)
            if not self.seeded:continue
            x=self.features(t,qv,ca,end).reshape(1,-1)
            pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
            desired=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))))
            p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9
            desired=max(desired,legal);desired=min(desired,12.)
            sent=desired
            if self.expDebt>v1.EPS and self.would_expand(side,desired):
                alpha=self.repair_progress()
                self.blendAttempts+=1;self.alphaHist.append(alpha);self.desiredQtyHist.append(desired)
                # Bumpless/reference-governor style interpolation: incoming dominant-side
                # responsibility receives only the fraction justified by own materialized repair progress.
                sent=desired*alpha
                if sent+v1.EPS<legal:
                    self.blendHeldBelowMin+=1
                    self.sentQtyHist.append(0.0)
                    continue
                sent=min(sent,12.)
                self.blendSubmits+=1;self.sentQtyHist.append(sent)
            self.submit(t,side,p,sent)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {
            'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,
            'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,
            'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),
            'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],
            'blendAttempts':self.blendAttempts,'blendSubmits':self.blendSubmits,
            'blendHeldBelowMin':self.blendHeldBelowMin,
            'meanBlendAlpha':statistics.mean(self.alphaHist) if self.alphaHist else None,
            'meanDesiredBlendQty':statistics.mean(self.desiredQtyHist) if self.desiredQtyHist else None,
            'meanSentBlendQty':statistics.mean(self.sentQtyHist) if self.sentQtyHist else None,
        }

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    def mn(k):
        z=[r[k] for r in rs if r.get(k) is not None];return statistics.mean(z) if z else None
    return {
        'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),
        'pnl':p,'buyNotional':b,'roi':p/b if b else None,
        'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':mn('buyNotional'),
        'meanPairCoverage':mn('pairCoverage'),'meanAbsNet':mn('absNet'),'meanSubmits':mn('submits'),'meanFills':mn('fills'),
        'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),
        'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),
        'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),
        'meanBlendAttempts':mn('blendAttempts'),'meanBlendSubmits':mn('blendSubmits'),
        'meanBlendHeldBelowMin':mn('blendHeldBelowMin'),'meanBlendAlpha':mn('meanBlendAlpha'),
        'meanDesiredBlendQty':mn('meanDesiredBlendQty'),'meanSentBlendQty':mn('meanSentBlendQty'),
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_bumpless_linear_blend_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']
        traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj)
        test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=BumplessBlendSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_blend(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows)
        out={
            'version':'ETH_BUMPLESS_LINEAR_REEXPAND_BLEND_V1',
            'researchOnly':True,
            'boundary':[
                'Base = frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation',
                'No Target numeric threshold/classifier at runtime or training change',
                'Only dominant-side re-expansion while own expansion debt is outstanding is modified',
                'Weak-side repair is never reduced by the bumpless layer',
                'Incoming re-expansion desired qty is linearly blended by own materialized repair progress alpha in [0,1]',
                'If blended qty is below the exchange minimum notional quantity, HOLD; never round it upward',
                '<=180s no new exposure; Fresh101 development-only'
            ],
            'matureResearchMapping':{
                'bumplessTransfer':'continuous/interpolated authority transfer rather than abrupt controller switching',
                'referenceGovernor':'preserve nominal DAgger command and minimally modify desired responsibility only during constrained transition',
                'antiWindupTracking':'transition state is driven by authoritative materialized own fills rather than stale Target values'
            },
            'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows
        }
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
