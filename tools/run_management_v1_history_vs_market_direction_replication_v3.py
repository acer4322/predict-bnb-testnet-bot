from __future__ import annotations
import argparse,importlib.util,json,os,tempfile,zipfile
from pathlib import Path
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_management_v1_history_vs_market_direction_threeway_fork_v2.py'
if STAGED.exists():
    sp=importlib.util.spec_from_file_location('histdir_v2',STAGED);f2=importlib.util.module_from_spec(sp);sp.loader.exec_module(f2)
else:
    import tools.run_management_v1_history_vs_market_direction_threeway_fork_v2 as f2

class ForkExec(f2.Fork):
    def run_branch(self):
        r=super().run_branch();intr=r.get('intervention') or {}
        keys=[str(x.get('key')) for x in intr.get('newSlotEvents',[]) if x.get('event')=='ROLE_SLOT_SUBMIT' and x.get('key')]
        fills=[x for x in self.fill_side_sequence if str(x.get('key')) in set(keys)]
        r['targetExecution']={'keys':keys,'physicalFillQty':sum(float(x.get('incQty') or 0) for x in fills),'firstFillT':min([int(x.get('t')) for x in fills],default=None),'fillEvents':f2.norm(fills),'ordersAtEnd':{k:f2.norm(self.orders.get(k)) for k in keys}}
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=13);a=ap.parse_args()
    scan=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=(scan.get('firstEligiblePerMarket') or scan.get('smokeCandidates') or [])[:a.max_states];specs=[s for s in specs if not bool(s.get('qPendingActive'))]
    if not specs:raise RuntimeError('no eligible specs after pending-active exclusion')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='hist_dir_rep_v3_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tt=int(s['t']);tape=root/'tapes'/f'{mid}.json.xz';rr={}
            for b in ('MARKET_DIRECTION','HISTORY_DIRECTION','HOLD'):
                sim=ForkExec(tape,s,b)
                try:rr[b]=sim.run_branch()
                finally:sim.close()
            digs={b:rr[b].get('prefixDigest') for b in rr};m={b:f2.tm(rr[b]) for b in rr};ctrl=rr['MARKET_DIRECTION']
            checks={'prefixParity':len(set(digs.values()))==1 and None not in digs.values(),'marketSubmitExercised':bool((rr['MARKET_DIRECTION'].get('intervention') or {}).get('submitOnMarketSide')),'historySubmitExercised':bool((rr['HISTORY_DIRECTION'].get('intervention') or {}).get('submitOnHistorySide')),'holdApplied':bool((rr['HOLD'].get('intervention') or {}).get('holdApplied')),'allLedgerClean':all(not m[b]['ledgerViolations'] for b in m),'allMax4':all(m[b]['maxSlots']<=4 for b in m)}
            local={str(h):{b:f2.dv(f2.at(ctrl,tt,h),f2.at(rr[b],tt,h)) for b in ('HISTORY_DIRECTION','HOLD')} for h in f2.H}
            term={b:{k:m[b][k]-m['MARKET_DIRECTION'][k] for k in ('floor','best','gap','fills','submits','alternations','buyNotional','activeSubmits','managedRepairQty')} for b in ('HISTORY_DIRECTION','HOLD')}
            row={'marketId':mid,'t':tt,'stateSpec':s,'prefixDigests':digs,'interventions':{b:rr[b].get('intervention') for b in rr},'targetExecution':{b:rr[b].get('targetExecution') for b in rr},'checks':checks,'localDeltaVsMarketDirection':local,'terminalMetrics':m,'terminalDeltaVsMarketDirection':term,'valid':all(checks.values())};rows.append(row)
            print(json.dumps({'progress':i,'marketId':mid,'valid':row['valid'],'marketFill':row['targetExecution']['MARKET_DIRECTION']['physicalFillQty'],'historyFill':row['targetExecution']['HISTORY_DIRECTION']['physicalFillQty'],'terminalDelta':term},ensure_ascii=False),flush=True)
    valid=[r for r in rows if r['valid']]
    def sums(branch,key):return sum(float(r['terminalDeltaVsMarketDirection'][branch][key]) for r in valid)
    agg={'states':len(rows),'validStates':len(valid),'marketTargetFilledStates':sum(r['targetExecution']['MARKET_DIRECTION']['physicalFillQty']>f2.EPS for r in valid),'historyTargetFilledStates':sum(r['targetExecution']['HISTORY_DIRECTION']['physicalFillQty']>f2.EPS for r in valid),'bothTargetFilledStates':sum(r['targetExecution']['MARKET_DIRECTION']['physicalFillQty']>f2.EPS and r['targetExecution']['HISTORY_DIRECTION']['physicalFillQty']>f2.EPS for r in valid),'historyMinusMarket':{'sumDeltaFloor':sums('HISTORY_DIRECTION','floor'),'sumDeltaBest':sums('HISTORY_DIRECTION','best'),'sumDeltaGap':sums('HISTORY_DIRECTION','gap'),'sumDeltaFills':sums('HISTORY_DIRECTION','fills'),'sumDeltaBuyNotional':sums('HISTORY_DIRECTION','buyNotional'),'floorImprovedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['floor']>f2.EPS for r in valid),'floorHarmedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['floor']<-f2.EPS for r in valid),'bestImprovedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['best']>f2.EPS for r in valid),'bestHarmedStates':sum(r['terminalDeltaVsMarketDirection']['HISTORY_DIRECTION']['best']<-f2.EPS for r in valid)},'holdMinusMarket':{'sumDeltaFloor':sums('HOLD','floor'),'sumDeltaBest':sums('HOLD','best'),'sumDeltaGap':sums('HOLD','gap')}}
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_VS_MARKET_DIRECTION_REPLICATION_V3','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'stateCount':len(rows),'allCorrectnessPass':all(r['valid'] for r in rows),'aggregate':agg,'rows':rows,'boundary':['first outcome-blind eligible state per consumed market','same strict-past V3B prefix all branches','MARKET_DIRECTION unchanged current V3B','HISTORY_DIRECTION overrides only _direction for one target receipt; V3B retains role/qty/route/ledger semantics','HOLD suppresses one target open decision only','target submit and physical fill tracked separately','local vector outcomes 0.5/1/2/5/10s and terminal diagnostics','no winner/Target future in selection','realistic HFT/exact FIFO/max4/no dream fill/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'aggregate':agg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
