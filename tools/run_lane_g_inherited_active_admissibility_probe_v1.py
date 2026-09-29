from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2;EPS=1e-9
TARGETS={1946317:1788534350631,1946640:1788535906741}

class Probe(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,mid):super().__init__(tape,1,4);self.mid=mid;self.rows=[]
    def _try_r263(self,t,end):
        if int(t)==int(TARGETS[self.mid]):
            pf=list(getattr(self,'pendingFailure',[]));front=pf[0] if pf else None
            side=self._repair_side();qv=v2.base.quotes(self.book);ask=(qv.get(side,{}).get('ask') if qv and side else None)
            q=(1.0/float(ask) if ask not in (None,0) else None);debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
            before=float(self._physical_floor());after=(float(self._candidate_alone_floor(side,float(ask),float(q))) if q is not None else None)
            epoch=(int(front.get('generation')),(int(front.get('repairProgressClock')))) if front else None
            cond={
              'hasPendingFailure':bool(front),
              'frontGenerationMatches':bool(front and int(front.get('generation'))==int(self.scopeGeneration)),
              'frontSideMatches':bool(front and str(front.get('side'))==str(side)),
              'epochUnused':bool(front and epoch not in getattr(self,'usedEpochs',set())),
              'noLiveActive':not bool(self._has_live_active()),
              'activeAskAvailable':ask is not None,
              'activeMinQtyLegal':bool(q is not None and math.isfinite(q) and q>EPS and q<=12.0+EPS),
              'residualDebtCoversActiveMin':bool(q is not None and avail+EPS>=q),
              'activeFloorImproves':bool(after is not None and after>before+EPS),
            }
            would=all(cond.values())
            self.rows.append({'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,'repairSide':side,'pendingFailureCount':len(pf),'frontFailure':front,'usedEpochs':[list(x) for x in getattr(self,'usedEpochs',set())],'activeKeys':sorted(list(getattr(self,'activeKeys',set()))),'ask':ask,'activeMinQty':q,'debt':debt,'reserved':reserved,'availableResidualDebt':avail,'floorBefore':before,'activeCandidateFloor':after,'conditions':cond,'inheritedActiveAdmissibleIfInvokedAtManagerClock':would})
        return super()._try_r263(t,end)
    def run_probe(self,w):
        r=super().run_r264(w);return {'marketId':self.mid,'correct':bool(r.get('r264CorrectnessPass')),'rows':self.rows}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_active_probe_'))
    try:
      with zipfile.ZipFile(a.bundle) as z:
        co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
        for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
      rows=[]
      for m in mids:
        s=Probe(tmp/f'{m}.json.xz',m)
        try:r=s.run_probe(co[m]['winner'])
        finally:s.close()
        rows.append(r);print(json.dumps(r,ensure_ascii=False),flush=True)
      out={'version':'LANE_G_INHERITED_ACTIVE_ADMISSIBILITY_PROBE_V1_20260907','researchOnly':True,'behaviorChange':False,'rows':rows,'gates':{'correctnessPass':all(x['correct'] for x in rows),'anyInheritedActiveAdmissible':any(y.get('inheritedActiveAdmissibleIfInvokedAtManagerClock') for x in rows for y in x['rows'])},'boundary':['exact frozen R2.64 behavior','probe at V1B exact pre-R263 clock','no Active submit injected','existing FailureEvidenceActiveDrain conditions only','no timing threshold/new trigger','consumed only','no 8781']}
      op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
