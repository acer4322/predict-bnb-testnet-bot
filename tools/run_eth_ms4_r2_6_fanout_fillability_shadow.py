from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_6_parallel_passive_coverage.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r26',_STAGED);r26=importlib.util.module_from_spec(sp);sp.loader.exec_module(r26)
else:
    import tools.run_eth_ms4_r2_6_parallel_passive_coverage as r26
r22=r26.r22;r1=r26.r1;v2=r26.v2;EPS=1e-9

class FanoutFillabilityShadowSim(r26.ParallelPassiveCoverageSim):
    def __init__(self,tape,model,max_slots=4):
        super().__init__(tape,max_slots);self.execModel=model;self.fanoutShadow=[]

    def _parallel_repair_fill(self,t:int,side:str):
        made=0
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:
            self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        while len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
            used=self._used_prices(side);chosen=None
            for raw in self._live_price_levels(side):
                p=float(v2.kprice(raw))
                if p in used or p<=EPS:continue
                q=1.0/p
                if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
                sp=self._repair_split(side,p,q)
                if sp is None:continue
                if float(sp.get('overflowQty') or 0.0)>EPS:
                    self.r26['FANOUT_OVERFLOW_NOT_ALLOWED']+=1;continue
                chosen=(p,q,sp);break
            if chosen is None:
                self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;break
            p,q,sp=chosen
            score,diag=self._score(side,'SATELLITE_REPAIR',p,q,sp)
            before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
                self.r26['FANOUT_SUBMIT_BLOCKED']+=1;break
            key=f'{side}_{before_n}';self.fanoutKeys.add(key);made+=1;self.r26['FANOUT_SUBMIT']+=1
            rec={'t':int(t),'key':key,'generation':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks),'side':side,'price':float(p),'qty':float(q),'fillability':float(score),'rank':diag.get('rank'),'depth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'bestDepth':diag.get('bestDepth'),'pairSum':diag.get('pairSum'),'floorBefore':float(self._physical_floor()),'debtQty':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)}
            self.fanoutShadow.append(rec)
            ev={'t':int(t),'event':'PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)}
            self.r26events.append(ev);self.slot_history.append(ev)
        return made

    def run_shadow(self,winner):
        r=super().run_r26(winner)
        for z in self.fanoutShadow:
            k=z['key'];a=float(self.keyRepairQuotaAuthorized.get(k,0.0));rem=float(self.keyRepairQuotaRemaining.get(k,a));fill=max(0.0,a-rem)
            z['repairFillQty']=float(fill);z['filledAny']=bool(fill>EPS)
        r['fanoutFillabilityShadow']=self.fanoutShadow[:1500]
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='ms4_r26_shadow_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';sim=FanoutFillabilityShadowSim(tape,model,4)
            try:r=sim.run_shadow(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r})
            sh=r['fanoutFillabilityShadow'];filled=[x for x in sh if x['filledAny']];unfilled=[x for x in sh if not x['filledAny']]
            print(json.dumps({'marketId':mid,'fanout':len(sh),'fanoutFilled':len(filled),'meanScoreFilled':sum(x['fillability'] for x in filled)/len(filled) if filled else None,'meanScoreUnfilled':sum(x['fillability'] for x in unfilled)/len(unfilled) if unfilled else None,'scores':[round(x['fillability'],4) for x in sh],'labels':[int(x['filledAny']) for x in sh]},ensure_ascii=False),flush=True)
        allz=[dict(x,marketId=r['marketId']) for r in rows for x in r.get('fanoutFillabilityShadow',[])]
        out={'version':'MS4_R2_6_FANOUT_FILLABILITY_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'fanoutRows':allz,'boundary':['R2.6 behavior frozen; scoring is shadow only','OUR realistic-HFT fillability model only ranks execution realization','no Target/winner input to model','no responsibility/risk/qty/action authority','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'fanoutRows':len(allz),'filled':sum(x['filledAny'] for x in allz)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
