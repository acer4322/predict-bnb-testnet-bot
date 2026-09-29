from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_pair_only_repair_floor_value_smoke as v1
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
EPS=1e-9

class NegativeFloorRepairOverrideSim(v1.PairOnlyRepairFloorValueSim):
    def _repair_floor_value_ok(self,side,p,q):
        self.repairFloorValueChecks+=1
        before=self._physical_floor();after=self._project_physical_floor_full_fill(side,p,q)
        return (before < -EPS and after > before + EPS),after,before


def slim2(r):
    s=base.slim(r);s.update({'repairFloorValueChecks':int(r.get('repairFloorValueChecks') or 0),'repairFloorValueOverrides':int(r.get('repairFloorValueOverrides') or 0),'repairFloorValueBlocks':int(r.get('repairFloorValueBlocks') or 0)})
    return s


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='repair_floor_neg_only_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=NegativeFloorRepairOverrideSim(tape,4)
            try:rr=c.run_candidate(cr['winner'])
            finally:c.close()
            bs,cs=base.slim(br),slim2(rr);rows.append({'marketId':mid,'baseline':bs,'candidate':cs})
            print(json.dumps({'marketId':mid,'baseline':bs,'candidate':cs},ensure_ascii=False),flush=True)
        def agg(which):
            xs=[r[which] for r in rows];return {'markets':len(xs),'totalFills':sum(x['fills'] for x in xs),'avgFills':sum(x['fills'] for x in xs)/len(xs),'totalAlts':sum(x['fillSideAlternations'] for x in xs),'avgAlts':sum(x['fillSideAlternations'] for x in xs)/len(xs),'twoSided':sum(x['twoSidedMaterialized'] for x in xs),'totalPnl':sum(x['pnl'] for x in xs),'avgPnl':sum(x['pnl'] for x in xs)/len(xs),'avgFloor':sum(x['floor'] for x in xs)/len(xs)}
        out={'version':'ETH_PAIR_ONLY_REPAIR_FLOOR_VALUE_NEGATIVE_ONLY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('baseline'),'candidate':agg('candidate')},'rows':rows,'boundary':['Pair economics unchanged for all pair-compatible actions','pair>1 Repair/Core override only when current physical Floor is negative and deterministic full-fill Floor strictly improves','no threshold beyond zero-Floor economic boundary','no serialization/shared Floor budget/risk contraction/one-new-per-receipt veto','max4 and <=180s retained','realistic HFT; no Target runtime; no dream fill; no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
