from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1;v2=r1.v2;EPS=1e-9

class SoftPairPreferenceSim(r28.FanoutRoleCapacitySim):
    """R2.11: economic quality is a ranking preference, never an admission veto.
    Candidate universe remains the existing nearest live levels and all frozen Repair/Overflow checks.
    """
    def __init__(self,tape,mode,max_slots=4):
        super().__init__(tape,1,max_slots);self.mode=str(mode).upper();self.pref=Counter();self.prefEvents=[]
    def _fifo_unmatched(self):
        un={'UP':[],'DOWN':[]}
        for e in self.slot_history:
            if e.get('event')!='ROLE_FILL_SPLIT':continue
            side=str(e['side']);opp='DOWN' if side=='UP' else 'UP';rem=float(e.get('fillInc') or 0.0);p=float(e['price'])
            while rem>EPS and un[opp]:
                lot=un[opp][0];m=min(rem,lot['qty']);rem-=m;lot['qty']-=m
                if lot['qty']<=EPS:un[opp].pop(0)
            if rem>EPS:un[side].append({'qty':rem,'price':p})
        return un
    def _damage(self,side,p,q):
        un=self._fifo_unmatched();opp='DOWN' if side=='UP' else 'UP';lots=[dict(x) for x in un[opp]];rem=float(q);bad=good=paired=cost=0.0
        while rem>EPS and lots:
            lot=lots[0];m=min(rem,lot['qty']);ps=float(lot['price'])+float(p);paired+=m;cost+=m*ps;bad+=m*max(0.0,ps-1.0);good+=m*max(0.0,1.0-ps);rem-=m;lot['qty']-=m
            if lot['qty']<=EPS:lots.pop(0)
        return bad,good,(cost/paired if paired>EPS else None)
    def _ranked_repair_candidates(self,side,limit=4):
        used=self._used_prices(side);out=[]
        for raw in self._live_price_levels(side):
            p=float(v2.kprice(raw))
            if p in used or p<=EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            sp=self._repair_split(side,p,q)
            if sp is None:continue
            bad,good,wps=self._damage(side,p,float(sp['repairQty']))
            out.append({'p':p,'q':q,'sp':sp,'bad':bad,'good':good,'wps':wps,'rank':len(out)+1})
            if len(out)>=int(limit):break
        return out
    def _choose_pref(self,side,context):
        arr=self._ranked_repair_candidates(side,self.max_slots)
        if not arr:return None
        original=arr[0];chosen=min(arr,key=lambda x:(x['bad'],-x['good'],x['rank']))
        self.pref['PREFERENCE_CHECK']+=1
        if chosen['p']!=original['p']:self.pref['PRICE_REORDERED']+=1
        ev={'event':'SOFT_PAIR_PRICE_PREFERENCE','context':context,'side':side,'originalPrice':original['p'],'chosenPrice':chosen['p'],'originalPairDamage':original['bad'],'chosenPairDamage':chosen['bad'],'originalPairSum':original['wps'],'chosenPairSum':chosen['wps'],'candidateCount':len(arr),'chosenRank':chosen['rank']}
        self.prefEvents.append(ev)
        return chosen
    def _candidate_from_levels_v8(self,side,role,require_pair):
        # Core and non-Repair semantics stay exactly frozen.
        if role!='SATELLITE_REPAIR' or self.mode!='ALL_PASSIVE_REPAIR':
            return super()._candidate_from_levels_v8(side,role,require_pair)
        ch=self._choose_pref(side,'NATIVE_SATELLITE')
        if ch is None:return None
        return float(ch['p']),float(ch['q']),float(ch['sp']['fullFloor']),ch['sp']
    def _parallel_repair_fill(self,t:int,side:str):
        # Preserve CAP1 role occupancy; only change price ranking for the one fanout carrier.
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:
            self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        live_fan=sum(1 for _,k,o,role in self._live_role_rows(side=side) if k in self.fanoutKeys)
        if live_fan>=1:
            self.capacityBlocks+=1;return 0
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return 0
        ch=self._choose_pref(side,'PARALLEL_FANOUT')
        if ch is None:
            self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;return 0
        # R2.6 fanout is pure Repair only; if preference candidate carries overflow, fall back to original CAP1 path.
        if float(ch['sp'].get('overflowQty') or 0.0)>EPS:
            return super()._parallel_repair_fill(t,side)
        p,q,sp=ch['p'],ch['q'],ch['sp'];before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
            self.r26['FANOUT_SUBMIT_BLOCKED']+=1;return 0
        key=f'{side}_{before_n}';self.fanoutKeys.add(key);self.r26['FANOUT_SUBMIT']+=1
        ev={'t':int(t),'event':'PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)}
        self.r26events.append(ev);self.slot_history.append(ev);return 1
    def run_pref(self,w):
        r=super().run_cap(w);r['softPairPreferenceStats']=dict(self.pref);r['softPairPreferenceEvents']=self.prefEvents[:1500];r['softPairMode']=self.mode;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r211_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:r0=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0})
            for mode in ['FANOUT_ONLY','ALL_PASSIVE_REPAIR']:
                s=SoftPairPreferenceSim(tape,mode,4)
                try:r=s.run_pref(cr['winner'])
                finally:s.close()
                rows.append({'marketId':mid,'cell':'MS4_R211_'+mode,'winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'marketId':mid,'cells':{r['cell']:{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'fan':r.get('parallelPassiveFanoutSubmits',0),'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'pref':r.get('softPairPreferenceStats',{})} for r in rows if r['marketId']==mid},'unauth':max(float(r.get('unauthorizedOverflowQty',0) or 0) for r in rows if r['marketId']==mid),'quotaExcess':max(float(r.get('repairQuotaExcessMax',0) or 0) for r in rows if r['marketId']==mid)},ensure_ascii=False),flush=True)
        C={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};cmp={}
        for cell in ['MS4_R211_FANOUT_ONLY','MS4_R211_ALL_PASSIVE_REPAIR']:
            N={r['marketId']:r for r in rows if r['cell']==cell};arr=[]
            for m in mids:
                b,n=C[m],N[m];arr.append({'marketId':m,'fillDelta':n['fillEvents']-b['fillEvents'],'fillRetention':n['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':n['submits']-b['submits'],'pnlDelta':n['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':n['floor']-b['floor'],'reordered':n.get('softPairPreferenceStats',{}).get('PRICE_REORDERED',0)})
            cmp[cell]=arr
        correctness=all(float(r.get('unauthorizedOverflowQty',0) or 0)<=EPS and float(r.get('repairQuotaExcessMax',0) or 0)<=EPS for r in rows if r['cell']!='MS4_R28_CAP1_CONTROL')
        out={'version':'MS4_R2_11_SOFT_PAIR_PREFERENCE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correctness},'boundary':['R2.8 CAP1 max_slots=4 and role occupancy frozen','no pairSum admission veto','price preference only within up to max_slots nearest currently legal Repair live-price candidates','Economic Core unchanged','Repair/Overflow split and reservation unchanged','Active routing unchanged','FANOUT_ONLY changes only parallel fanout price ranking','ALL_PASSIVE_REPAIR also changes native Satellite Repair price ranking','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
