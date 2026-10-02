from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import tools.run_eth_role_separated_multislot_v3_smoke as v3
v2=v3.v2
EPS=1e-9

class MinimalPairRoleSim(v3.RoleSeparatedMultiSlotSim):
    """Research-only role-separated controller with only Pair economics as hard strategy safety.

    Retains max-slot structural capacity, live distinct-price geometry, queue/reanchor lifecycle,
    realistic HFT execution legality, and <=180s market-time boundary. Removes shared-Floor budget,
    shared-budget contraction, and one-new-per-receipt throttle from action authority.
    Optional same-side serialization is a matched comparator only.
    """
    def __init__(self,tape,max_slots:int=4,serialize_same_side:bool=False):
        super().__init__(tape,max_slots)
        self.serialize_same_side=bool(serialize_same_side)
        self.minimal_pair_checks=0;self.minimal_pair_blocks=0;self.serialization_blocks=0
        self.fill_side_sequence=[];self.fill_key_seen={}

    def process(self,t):
        before={key:float(o.get('cum') or 0.0) for key,o in self.orders.items()}
        super().process(t)
        for key,o in self.orders.items():
            cur=float(o.get('cum') or 0.0);old=float(self.fill_key_seen.get(key,0.0))
            if cur>old+EPS:
                self.fill_key_seen[key]=cur
                self.fill_side_sequence.append({'t':int(t),'key':key,'side':str(o.get('side')),'price':float(o.get('price') or 0.0),'incQty':cur-old,'role':self.key_role.get(key,'UNASSIGNED')})

    def _role_decision(self,qv):
        side,role,_,_=super()._role_decision(qv)
        # All action roles use the same deterministic Pair-economics hard rule.
        return side,role,True,False

    def _candidate_from_levels(self,side:str,require_pair:bool=True,require_budget:bool=False):
        used=self._used_prices(side)
        for p in self._live_price_levels(side):
            p=v2.kprice(p)
            if p in used:continue
            q=1.0/p
            self.minimal_pair_checks+=1
            if not self._pair_ok(side,p):
                self.minimal_pair_blocks+=1;self.veto['MINIMAL_PAIR_ECONOMICS']+=1;continue
            return float(p),float(q),None
        return None

    def _same_side_reserved(self,side:str)->bool:
        return any(str(o.get('side'))==str(side) for _,_,o,_ in self._live_role_rows())

    def _submit_role(self,t:int,side:str,role:str,p:float,q:float,proj,source:str):
        # Remove one-new-per-receipt strategy throttle. Max4 remains a structural execution capacity.
        if len(self.slot_key)>=self.max_slots:
            self.veto['GLOBAL_SLOT_CAP_FULL']+=1;return False
        if self.serialize_same_side and self._same_side_reserved(side):
            self.serialization_blocks+=1;self.veto['SAME_SIDE_SERIALIZATION']+=1;return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return False
        before_n=self.n;self.submit(int(t),side,float(p),float(q));key=f'{side}_{before_n}'
        self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,p,q);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        state,held,_=self._state()
        if state=='ONE_SIDED' and role=='ECONOMIC_CORE':self.prebase_core_submits+=1
        if state=='ONE_SIDED' and role=='SATELLITE_EXPAND' and side==held:self.prebase_same_side_satellite_submits+=1
        self.slot_history.append({'t':int(t),'event':'ROLE_SLOT_SUBMIT','slotId':int(free),'key':key,'role':role,'side':side,'price':float(p),'qty':float(q),'jointProjectedFloor':None,'physicalFloor':self._physical_floor(),'source':source})
        return True

    def _risk_contract_if_needed(self,t:int):
        # Diagnostic-only in minimal pair lane; never cancels or blocks.
        return

    def run_minimal(self,winner):
        r=self.run_v3(winner)
        seq=[x['side'] for x in self.fill_side_sequence]
        alt=sum(1 for i in range(1,len(seq)) if seq[i]!=seq[i-1])
        r.update({
            'minimalPairSafety':True,
            'sameSideSerialization':self.serialize_same_side,
            'minimalPairChecks':int(self.minimal_pair_checks),
            'minimalPairBlocks':int(self.minimal_pair_blocks),
            'serializationBlocks':int(self.serialization_blocks),
            'fillSideAlternations':int(alt),
            'fillSideSequence':self.fill_side_sequence[:1000],
            'twoSidedMaterialized':bool(float(self.inv['UP'])>EPS and float(self.inv['DOWN'])>EPS),
        })
        return r

def slim(r):
    return {
        'submits':int(r.get('submits') or 0),'fills':int(r.get('fillEvents') or 0),
        'filledQty':float(r.get('filledQty') or 0.0),'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),
        'floor':float(r.get('floor') or 0.0),'best':float(r.get('best') or 0.0),
        'maxSlots':int(r.get('maxSimultaneousSlots') or 0),'maxDistinct':int(r.get('maxSimultaneousDistinctPrices') or 0),
        'roleSubmits':r.get('roleSubmits') or {},'roleFills':r.get('roleFills') or {},
        'roleFillQty':r.get('roleFillQty') or {},'pairChecks':int(r.get('minimalPairChecks') or 0),
        'pairBlocks':int(r.get('minimalPairBlocks') or 0),'serializationBlocks':int(r.get('serializationBlocks') or 0),
        'fillSideAlternations':int(r.get('fillSideAlternations') or 0),'twoSidedMaterialized':bool(r.get('twoSidedMaterialized')),
        'reanchors':int(r.get('reanchors') or 0),'veto':r.get('vetoCounts') or {},
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='role_min_pair_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            control=v3.RoleSeparatedMultiSlotSim(tape,4)
            try:r0=control.run_v3(cr['winner'])
            finally:control.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':'A_V3_FULL_ROLE_SAFETY',**r0})
            for label,serial in [('B_PAIR_ONLY_MAX4',False),('C_PAIR_PLUS_SERIALIZATION_MAX4',True)]:
                sim=MinimalPairRoleSim(tape,4,serial)
                try:r=sim.run_minimal(cr['winner'])
                finally:sim.close()
                rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'cell':label,**r})
                print(json.dumps({'progress':mid,'cell':label,**slim(r)},ensure_ascii=False),flush=True)
        cells=['A_V3_FULL_ROLE_SAFETY','B_PAIR_ONLY_MAX4','C_PAIR_PLUS_SERIALIZATION_MAX4']
        summary={}
        for cell in cells:
            xs=[x for x in rows if x['cell']==cell]
            pn=[float(x.get('pnlDiagnosticOnly') or 0.0) for x in xs]
            summary[cell]={
                'markets':len(xs),'totalSubmits':sum(int(x.get('submits') or 0) for x in xs),'totalFills':sum(int(x.get('fillEvents') or 0) for x in xs),
                'avgSubmits':sum(int(x.get('submits') or 0) for x in xs)/len(xs),'avgFills':sum(int(x.get('fillEvents') or 0) for x in xs)/len(xs),
                'tradeCoverage':sum(int(x.get('fillEvents') or 0)>0 for x in xs)/len(xs),
                'twoSidedCoverage':sum(bool(x.get('twoSidedMaterialized')) for x in xs)/len(xs) if cell!='A_V3_FULL_ROLE_SAFETY' else None,
                'totalAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs),
                'avgAlternations':sum(int(x.get('fillSideAlternations') or 0) for x in xs)/len(xs),
                'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs),'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),
                'avgFloor':sum(float(x.get('floor') or 0.0) for x in xs)/len(xs),
            }
        out={'version':'ETH_ROLE_SEPARATED_MINIMAL_PAIR_SAFETY_SMOKE_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':summary,'rows':rows,
             'boundary':['actual role-separated distinct-price lifecycle runner','B hard strategy safety = Pair economics only','C hard strategy safety = Pair economics + same-side serialization only','shared Scoped-Floor budget and risk-contract cancellation disabled in B/C','one-new-per-receipt throttle disabled in B/C','max4 structural execution capacity retained','rolling live-price reanchor retained','<=180s market-time boundary retained','realistic HFT queue/250ms execution substrate','winner post-hoc only; no Target runtime input; no dream fill; no 8781','fill-side alternation is a liveness proxy, not semantic responsibility-round authority']}
        op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
