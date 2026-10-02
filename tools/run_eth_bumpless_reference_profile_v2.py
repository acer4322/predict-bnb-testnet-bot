from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

class BumplessReferenceProfileSim(lp.LocalReservedBootSim):
    def __init__(self,tape,mode,models):
        super().__init__(tape,mode,models)
        self.expDebt=0.0;self.peakDebt=0.0
        self.refActive=False;self.refSide=None;self.refStartNet=0.0;self.refGoalNet=0.0
        self.profileCreates=0;self.profileResets=0;self.profileAttempts=0;self.profileSubmits=0;self.profileHeldBelowMin=0
        self.alphaHist=[];self.errorHist=[]

    def net(self):return float(self.inv['UP'])-float(self.inv['DOWN'])

    def reset_profile(self):
        if self.refActive:self.profileResets+=1
        self.refActive=False;self.refSide=None;self.refStartNet=self.net();self.refGoalNet=self.net()

    def record_fill(self,t,side,q,p):
        pre=abs(self.net())
        super().record_fill(t,side,q,p)
        post=abs(self.net());delta=post-pre
        if delta>v1.EPS:
            self.expDebt=max(0.0,self.expDebt)+delta;self.peakDebt=max(self.peakDebt,self.expDebt)
        elif delta<-v1.EPS and self.expDebt>v1.EPS:
            self.expDebt=max(0.0,self.expDebt-(-delta))
            if self.expDebt<=v1.EPS:
                self.expDebt=0.0;self.peakDebt=0.0;self.reset_profile()

    def repair_progress(self):
        if self.expDebt<=v1.EPS or self.peakDebt<=v1.EPS:return 1.0
        return max(0.0,min(1.0,(self.peakDebt-self.expDebt)/self.peakDebt))

    def would_expand(self,side,qty):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);pre=abs(u-d)
        if side=='UP':u+=float(qty)
        else:d+=float(qty)
        return abs(u-d)>pre+v1.EPS

    def ensure_profile(self,side,desired):
        cur=self.net();sgn=1.0 if side=='UP' else -1.0
        if (not self.refActive) or self.refSide!=side:
            self.refActive=True;self.refSide=side;self.refStartNet=cur;self.refGoalNet=cur+sgn*float(desired);self.profileCreates+=1

    def governed_qty(self,side,desired):
        self.ensure_profile(side,desired)
        alpha=self.repair_progress();sgn=1.0 if side=='UP' else -1.0
        governed=self.refStartNet+alpha*(self.refGoalNet-self.refStartNet)
        cur=self.net();gap=sgn*(governed-cur)
        self.alphaHist.append(alpha);self.errorHist.append(max(0.0,gap))
        return max(0.0,min(float(desired),gap)),alpha

    def run_student_profile(self,models,winner):
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
            x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(models['action'].predict_proba(x)[0,1])
            if pa<models['actionTh']:continue
            ps=float(models['side'].predict_proba(x)[0,1]);side='UP' if ps>=models['sideTh'] else 'DOWN'
            desired=max(.01,float(np.expm1(np.clip(models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);legal=1/p if p>0 else 1e9
            desired=max(desired,legal);desired=min(desired,12.)
            sent=desired
            if self.expDebt>v1.EPS and self.would_expand(side,desired):
                self.profileAttempts+=1;sent,alpha=self.governed_qty(side,desired)
                if sent+v1.EPS<legal:
                    self.profileHeldBelowMin+=1;continue
                self.profileSubmits+=1
            else:
                # Weak-side repair is always allowed. Keep an existing expansion reference in shadow;
                # it will advance only when materialized repair changes alpha. If nominal expansion side flips,
                # ensure_profile() will reinitialize from authoritative current net at the next expansion request.
                sent=desired
            self.submit(t,side,p,sent)
        end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2)
        pnl=self.inv.get(str(winner).upper(),0.)-self.cost;gross=sum(self.inv.values());opp='UP' if str(winner).upper()=='DOWN' else 'DOWN'
        return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,
                'pairCoverage':2*min(self.inv.values())/gross if gross>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,
                'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'up':self.inv['UP'],'down':self.inv['DOWN'],
                'profileCreates':self.profileCreates,'profileResets':self.profileResets,'profileAttempts':self.profileAttempts,
                'profileSubmits':self.profileSubmits,'profileHeldBelowMin':self.profileHeldBelowMin,
                'meanProfileAlpha':statistics.mean(self.alphaHist) if self.alphaHist else None,
                'meanProfileGap':statistics.mean(self.errorHist) if self.errorHist else None}

def agg(rs):
    p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs)
    def mn(k):
        z=[r[k] for r in rs if r.get(k) is not None];return statistics.mean(z) if z else None
    return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,
            'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':mn('buyNotional'),'meanPairCoverage':mn('pairCoverage'),'meanAbsNet':mn('absNet'),
            'meanSubmits':mn('submits'),'meanFills':mn('fills'),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),
            'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),
            'meanProfileCreates':mn('profileCreates'),'meanProfileAttempts':mn('profileAttempts'),'meanProfileSubmits':mn('profileSubmits'),
            'meanProfileHeldBelowMin':mn('profileHeldBelowMin'),'meanProfileAlpha':mn('meanProfileAlpha'),'meanProfileGap':mn('meanProfileGap')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_bumpless_ref_profile_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'))
        models,off1,off2=lp.train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=BumplessReferenceProfileSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student_profile(models,cr['winner'])
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        s=agg(rows);out={'version':'ETH_BUMPLESS_REFERENCE_PROFILE_V2','researchOnly':True,
            'boundary':['Base = frozen two-round DAgger + BOOK_IMBALANCE + Local Pending Reservation','No Target numeric threshold/classifier','Only outstanding-debt dominant re-expansion is governed; weak-side repair always passes','At first nominal re-expansion request, freeze a reference profile from authoritative current net to the nominal one-action net goal','Governed net target moves from start to goal according to own materialized repair progress; repeated receipt ticks do not accumulate extra command','If profile gap is below legal minimum, HOLD until state progress moves the governed reference far enough; never round upward','Fresh101 development-only; <=180s no new exposure'],
            'matureResearchMapping':{'bumplessTransfer':'incoming mode follows a state-driven target profile rather than abrupt switch','referenceGovernor':'nominal DAgger reference is preserved but modified only enough to remain on the transition profile','antiWindupTracking':'reference is anchored to authoritative current net and does not accumulate every receipt tick'},
            'round1Offline':off1,'round2Offline':off2,'summary':s,'rows':rows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':s},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
