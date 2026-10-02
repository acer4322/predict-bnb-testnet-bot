from __future__ import annotations
import argparse, json, math, os, shutil, tempfile, zipfile, importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent

def sibling(name,filename):
    p=HERE/filename;s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
lad=sibling('pair_ladder_brake','run_eth_safety_reintroduction_ladder_1946317.py')
LadderSim=lad.LadderSim
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986]

class UnderwaterBrakeSim(LadderSim):
    def __init__(self,tape,enabled:bool):
        super().__init__(tape,1,True,False,False)
        self.brakeEnabled=bool(enabled);self.brakeEvents=[];self.blockedSameSideSubmits=0
    def _open_free_slots(self,t,qv,end):
        if self.brakeEnabled and int(end)-int(t)>lad.NO_NEW_EXPOSURE_MS:
            side=self._direction(qv);uq=sum(float(a) for a,_ in self.un[side])
            if uq>EPS:
                avg=self.unmatched_avg(side);bid=float(qv[side]['bid'])
                if avg is not None and bid<=float(avg)+1e-10:
                    self.blockedSameSideSubmits+=1
                    self.brakeEvents.append({'t':int(t),'side':side,'unmatchedQty':float(uq),'unmatchedAvgCost':float(avg),'currentBid':bid,'currentAsk':float(qv[side]['ask']),'activeHeadroom':bid-float(avg),'bookImbalance':float(qv.get('imb') or 0.0),'submitsBefore':int(self.submits),'fillsBefore':int(self.fills)})
                    self.veto['UNDERWATER_SAME_SIDE_ADD_BRAKE']+=1
                    return
        return super()._open_free_slots(t,qv,end)
    def run_brake(self,winner):
        r=self.run_ladder(winner);r.update({'blockedSameSideSubmits':int(self.blockedSameSideSubmits),'brakeEvents':self.brakeEvents[:400]});return r

def summary(rows,arm):
    xs=[r for r in rows if r['arm']==arm];pn=[float(r['pnl']) for r in xs];wins=[x for x in pn if x>EPS];fills=sum(int(r['fills']) for r in xs);bn=sum(float(r['buyNotional']) for r in xs);best=max(pn);worst=min(pn)
    return {'markets':len(xs),'aggregatePnl':sum(pn),'avgPnl':sum(pn)/len(xs),'positiveMarkets':len(wins),'winRate':len(wins)/len(xs),'worstPnl':worst,'bestPnl':best,'leaveOneBestOut':sum(pn)-best,'tradeCoverage':sum(int(r['fills'])>0 for r in xs)/len(xs),'totalBuyNotional':bn,'totalFills':fills,'blockedSameSideSubmits':sum(int(r.get('blockedSameSideSubmits') or 0) for r in xs),'exerciseMarkets':sum(int(r.get('blockedSameSideSubmits') or 0)>0 for r in xs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='mvps_uw_brake4_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in MIDS:
            for arm,en in [('N_PAIR1_MINIMAL',False),('B_UNDERWATER_SAME_SIDE_ADD_BRAKE',True)]:
                sim=UnderwaterBrakeSim(tmp/'tapes'/f'{mid}.json.xz',en)
                try:r=sim.run_brake(co[mid]['winner'])
                finally:sim.close()
                row={'marketId':mid,'winnerPostHocOnly':str(co[mid]['winner']).upper(),'arm':arm,'pnl':float(r['pnlDiagnosticOnly']),'floor':float(r['floor']),'best':float(r['best']),'fills':int(r['fillEvents']),'submits':int(r['submits']),'buyNotional':float(r['buyNotional']),'blockedSameSideSubmits':int(r.get('blockedSameSideSubmits') or 0),'brakeEvents':r.get('brakeEvents') or []}
                rows.append(row);print(json.dumps({'marketId':mid,'arm':arm,'pnl':row['pnl'],'fills':row['fills'],'notional':row['buyNotional'],'blocked':row['blockedSameSideSubmits'],'firstBrake':row['brakeEvents'][0] if row['brakeEvents'] else None},ensure_ascii=False),flush=True)
        sN=summary(rows,'N_PAIR1_MINIMAL');sB=summary(rows,'B_UNDERWATER_SAME_SIDE_ADD_BRAKE')
        # exact baseline references from frozen smoke4 are expected and checked numerically.
        expected={1945866:131.568490481977,1945869:-39.72549019607843,1945898:308.58546345055845,1945986:-107.39596435995786}
        parity={str(mid):abs(next(r['pnl'] for r in rows if r['marketId']==mid and r['arm']=='N_PAIR1_MINIMAL')-expected[mid])<=1e-9 for mid in MIDS}
        fillRetention=sB['totalFills']/sN['totalFills'] if sN['totalFills'] else None;notionalRetention=sB['totalBuyNotional']/sN['totalBuyNotional'] if sN['totalBuyNotional'] else None
        gates={'baselineParity':all(parity.values()),'exercise':sB['exerciseMarkets']>=1,'tradeCoverage4of4':sB['tradeCoverage']==1.0,'aggregatePositive':sB['aggregatePnl']>0,'worstImproved':sB['worstPnl']>sN['worstPnl']+1e-9,'leaveOneBestOutImproved':sB['leaveOneBestOut']>sN['leaveOneBestOut']+1e-9,'positiveMarketsNotLower':sB['positiveMarkets']>=sN['positiveMarkets'],'activityNotNearZero':fillRetention>=0.5 and notionalRetention>=0.5}
        go=all(gates.values())
        out={'version':'MVPS_PAIR1_UNDERWATER_SAME_SIDE_ADD_BRAKE_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'summary':{'baseline':sN,'candidate':sB,'delta':{'aggregatePnl':sB['aggregatePnl']-sN['aggregatePnl'],'worstPnl':sB['worstPnl']-sN['worstPnl'],'bestPnl':sB['bestPnl']-sN['bestPnl'],'leaveOneBestOut':sB['leaveOneBestOut']-sN['leaveOneBestOut'],'fillRetention':fillRetention,'notionalRetention':notionalRetention}},'baselineParity':parity,'gates':gates,'goExpandTo8':go,'boundary':['only same-side ADD blocked when selected side already has unmatched inventory and bid<=avg unmatched cost','opposite-side Pair repair unchanged','state automatically re-enables ADD when bid>avg cost','no fixed time/qty/exposure cap/no winner/PnL runtime input','Pair economics and <=180s retained','realistic HFT/no dream fill/no8781']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'gates':gates,'goExpandTo8':go},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
