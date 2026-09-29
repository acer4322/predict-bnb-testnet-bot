from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import defaultdict,deque,Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9

class PositiveCycleRearmCreditShadow(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.tranches=defaultdict(deque);self.allTranches=[];self.shadow=[];self.stats=Counter();self.r274End=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])

    def _new_risk_fills(self,events):
        for ev in events:
            if ev.get('event')!='ROLE_FILL_SPLIT':continue
            key=str(ev.get('key'));inc=float(ev.get('fillInc') or 0.0)
            if key not in self.riskTrancheMeta or inc<=EPS:continue
            m=self.riskTrancheMeta[key];gen=int(m.get('generation') or ev.get('generationAtSubmit') or -1)
            z={'id':len(self.allTranches)+1,'key':key,'generation':gen,'side':str(ev.get('side')),
               'entryPrice':float(ev.get('price') or m.get('price') or 0.0),'qty':inc,'remaining':inc,
               'passivePaid':0.0,'activePaid':0.0,'edge':0.0,'createdAt':int(ev.get('t') or 0),'completedAt':None}
            self.allTranches.append(z);self.tranches[gen].append(z);self.stats['RISK_FILL_TRANCHE']+=1

    def _eval_completion(self,t,z):
        self.stats['COMPLETED']+=1
        if z['edge']<-EPS:self.stats['NEGATIVE_COMPLETED']+=1;return
        self.stats['NONNEG_COMPLETED']+=1
        passive_only=z['passivePaid']>=z['qty']-EPS and z['activePaid']<=EPS
        if passive_only:self.stats['PASSIVE_ONLY_NONNEG']+=1
        same_scope=(self.scopeSide==z['side'] and int(self.scopeGeneration)==int(z['generation']))
        side=z['side'];available=float(self._available_expand_risk_credit()) if self.scopeSide is not None else 0.0
        cand=None;risk=None
        if same_scope:
            c=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
            if c is not None:
                p,q,proj,split=c
                risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)))
                cand={'price':float(p),'qty':float(q),'riskCost':float(risk)}
        cap=len(self.slot_key)+len(self.activeKeys)<self.max_slots
        stale=self._has_stale_scope_reservation()
        row={'t':int(t),'trancheId':z['id'],'generation':z['generation'],'side':side,'entryPrice':z['entryPrice'],'qty':z['qty'],
             'pairEdge':z['edge'],'passivePaid':z['passivePaid'],'activePaid':z['activePaid'],'passiveOnly':passive_only,
             'sameScopeGeneration':same_scope,'availableContinuationCredit':available,'candidate':cand,
             'creditEnough':bool(cand is not None and available+EPS>=float(cand['riskCost'])),
             'capacityFree':cap,'staleScope':stale,'secondsLeft':(int(self.r274End)-int(t))/1000.0}
        row['behaviorEligible']=bool(passive_only and same_scope and cand is not None and row['creditEnough'] and cap and not stale and int(self.r274End)-int(t)>v2.NO_NEW_EXPOSURE_MS)
        if row['behaviorEligible']:self.stats['BEHAVIOR_ELIGIBLE_EXISTING_CREDIT']+=1
        elif passive_only:self.stats['PASSIVE_TOKEN_NOT_EXISTING_CREDIT_ELIGIBLE']+=1
        self.shadow.append(row)

    def process(self,t):
        s0=len(self.splitEvents);e0=len(self.r257Events)
        super().process(t)
        new_split=self.splitEvents[s0:];new_pay=self.r257Events[e0:]
        self._new_risk_fills(new_split)
        lookup={(int(x.get('t') or 0),str(x.get('key'))):x for x in new_split if x.get('event')=='ROLE_FILL_SPLIT'}
        completed=[]
        for e in new_pay:
            if e.get('event')!='R257_RISK_REPAIR_OBLIGATION_PAYMENT':continue
            q=float(e.get('paid') or 0.0);gen=int(e.get('generation') or -1)
            if q<=EPS:continue
            se=lookup.get((int(e.get('t') or 0),str(e.get('key'))),{})
            price=float(se.get('price') or 0.0);active=bool(e.get('active'))
            dq=self.tranches[gen]
            while q>EPS and dq:
                z=dq[0]
                if z['remaining']<=EPS:dq.popleft();continue
                take=min(q,z['remaining']);z['remaining']-=take
                if active:z['activePaid']+=take
                else:z['passivePaid']+=take
                z['edge']+=take*(1.0-z['entryPrice']-price);q-=take
                if z['remaining']<=EPS:
                    z['remaining']=0.0;z['completedAt']=int(t);dq.popleft();completed.append(z)
        for z in completed:self._eval_completion(t,z)

    def run_shadow(self,w):
        r=super().run_r264(w);r.update({'r274ShadowStats':dict(self.stats),'r274Shadow':self.shadow[:500],'r274Tranches':self.allTranches[:500]});return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r274shadow_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];agg=Counter()
        for m in mids:
            s=PositiveCycleRearmCreditShadow(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(co[m]['winner'])
            finally:s.close()
            st=Counter(r['r274ShadowStats']);agg.update(st);row={'marketId':m,'pnl':r['pnlDiagnosticOnly'],'best':r['best'],'floor':r['floor'],'fills':r['fillEvents'],'stats':dict(st),'shadow':r['r274Shadow']};rows.append(row)
            print(json.dumps({'marketId':m,'stats':dict(st),'eligible':[x for x in r['r274Shadow'] if x['behaviorEligible']]},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_74_POSITIVE_CYCLE_REARM_EXISTING_CREDIT_SHADOW_V1','researchOnly':True,'behaviorChange':False,'markets':mids,'aggregate':dict(agg),'rows':rows,
             'boundary':['exact R2.64 behavior','actual risk fill tranche FIFO','actual R2.57 Repair payments','nonnegative completed cycle is objective signal only','rearm resource must be already-available R2.47 continuation credit','no extra capital minted','passive-only tracked','no action change','no Target/future/winner runtime input']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':dict(agg)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
