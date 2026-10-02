from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    _spec=importlib.util.spec_from_file_location('ms4r1',_STAGED);ms4r1=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(ms4r1)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as ms4r1
EPS=1e-9

class ExecutionOutcomeDumpSim(ms4r1.QueueAwareRepairRoutingSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots)
        self.execution_rows=[]
    def _level_rank(self,side,p):
        levels=[float(ms4r1.v2.kprice(x)) for x in self._live_price_levels(side)]
        kp=float(ms4r1.v2.kprice(p))
        try:return levels.index(kp)+1
        except ValueError:return None
    def _depth(self,side,p):
        try:return float(self._level_depth(side,float(p)))
        except Exception:return None
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        before_n=self.n
        levels=[float(ms4r1.v2.kprice(x)) for x in self._live_price_levels(side)]
        best=levels[0] if levels else None
        rank=self._level_rank(side,p)
        depth=self._depth(side,p)
        best_depth=self._depth(side,best) if best is not None else None
        opp='DOWN' if side=='UP' else 'UP';opp_avg=self.unmatched_avg(opp)
        floor=float(self._physical_floor());upside=float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost))
        row={'submitT':int(t),'key':f'{side}_{before_n}','side':side,'role':role,'price':float(p),'qty':float(q),
             'rank':rank,'depth':depth,'bestPrice':best,'bestDepth':best_depth,
             'distanceFromBest':(float(best)-float(p)) if best is not None else None,
             'distanceTicks':((float(best)-float(p))/0.01) if best is not None else None,
             'pairCompatible':bool(self._pair_ok(side,p)),'oppositeUnmatchedAvg':float(opp_avg) if opp_avg is not None else None,
             'pairSum':(float(opp_avg)+float(p)) if opp_avg is not None else None,
             'floor':floor,'upside':upside,'upsideGap':upside-floor,
             'upQty':float(self.inv['UP']),'downQty':float(self.inv['DOWN']),'cost':float(self.cost),
             'scopeDebtQty':float(self._scope_debt_qty()),'reservedRepairQuota':float(self._reserved_repair_quota()),
             'availableExpandRiskCredit':float(self._available_expand_risk_credit()),
             'liveSlots':int(len(self.slot_key)),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
             'repairQtyAuthorized':float(split.get('repairQty',0.0)) if isinstance(split,dict) else 0.0,
             'overflowQtyAuthorized':float(split.get('overflowQty',0.0)) if isinstance(split,dict) else (float(q) if role=='SATELLITE_EXPAND' else 0.0)}
        ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        if ok:self.execution_rows.append(row)
        return ok
    def run_dump(self,winner):
        r=super().run_v88(winner)
        fills={};releases={}
        for ev in r.get('slotHistory',[]):
            typ=ev.get('event');key=ev.get('key');t=int(ev.get('t') or 0)
            if not key:continue
            if typ in ('SLOT_FILL','ROLE_FILL_SPLIT'):
                inc=float(ev.get('fillInc') or 0.0)
                if inc>EPS:
                    z=fills.setdefault(key,{'firstFillT':t,'lastFillT':t,'fillQty':0.0,'fillEvents':0})
                    z['firstFillT']=min(z['firstFillT'],t);z['lastFillT']=max(z['lastFillT'],t)
                    if typ=='ROLE_FILL_SPLIT':z['fillQty']+=inc;z['fillEvents']+=1
            elif typ=='SLOT_RELEASE':releases[key]={'terminalT':t,'terminalStatus':str(ev.get('status') or ''),'terminalCum':float(ev.get('cum') or 0.0)}
        outrows=[]
        for row in self.execution_rows:
            f=fills.get(row['key']);rel=releases.get(row['key']);ff=int(f['firstFillT']) if f else None
            row={**row,'filledAny':bool(f and f.get('fillQty',0)>EPS),'fillQty':float(f.get('fillQty',0.0)) if f else 0.0,
                 'fillEvents':int(f.get('fillEvents',0)) if f else 0,'firstFillDelayMs':(ff-row['submitT']) if ff is not None else None,
                 'filledWithin5s':bool(ff is not None and ff-row['submitT']<=5000),'terminalT':rel.get('terminalT') if rel else None,
                 'terminalStatus':rel.get('terminalStatus') if rel else None,'terminalCum':rel.get('terminalCum') if rel else None,
                 'lifetimeMs':(int(rel['terminalT'])-int(row['submitT'])) if rel and rel.get('terminalT') is not None else None}
            outrows.append(row)
        r['executionRows']=outrows
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r2_execdump_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];sim=ExecutionOutcomeDumpSim(tmp/'tapes'/f'{mid}.json.xz',4)
            try:r=sim.run_dump(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r})
            er=r['executionRows'];print(json.dumps({'marketId':mid,'submits':r['submits'],'fills':r['fillEvents'],'executionRows':len(er),'filledOrders':sum(x['filledAny'] for x in er),'fill5s':sum(x['filledWithin5s'] for x in er),'rank1':sum(x.get('rank')==1 for x in er),'rank4plus':sum((x.get('rank') or 0)>=4 for x in er)},ensure_ascii=False),flush=True)
        allx=[x for r in rows for x in r['executionRows']]
        out={'version':'MS4_R2_OUR_HFT_EXECUTION_OUTCOME_DUMP_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
             'aggregate':{'orders':len(allx),'filledOrders':sum(x['filledAny'] for x in allx),'filledWithin5s':sum(x['filledWithin5s'] for x in allx)},
             'boundary':['Frozen MS4-R1 actions; instrumentation only','labels from realistic-HFT physical execution outcome','no dream fill','winner posthoc only and not in execution features','no Target runtime input','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
