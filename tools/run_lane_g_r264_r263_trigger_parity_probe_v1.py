from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9
TRIGGERS={1946317:(1788534350431,4),1946640:(1788535906498,2)}

class Probe(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,mid,*a,**kw):
        self.mid=int(mid);self.probe=[]
        super().__init__(tape,*a,**kw)
    def _try_r263(self,t,end):
        tt,gg=TRIGGERS[self.mid]
        hit=(int(t)==int(tt) and int(self.scopeGeneration)==int(gg))
        before=None
        if hit:
            ob=self._obligation_current();live=self._live_dedicated_repair_rows(int(self.scopeGeneration)) if ob else []
            before={'t':int(t),'generation':int(self.scopeGeneration),'scopeSide':self.scopeSide,
                    'obligation':None if not ob else {k:ob.get(k) for k in ['generation','outstanding','repaidQty','bornQty']},
                    'liveRepairKeys':[str(k) for k,_ in live],
                    'liveRepairQuota':sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live),
                    'slots':len(self.slot_key),'active':len(self.activeKeys),'r263GenerationUsed':int(self.scopeGeneration) in self.r263GenerationUsed}
        n0=int(self.n);s0=int(self.submits)
        ok=super()._try_r263(t,end)
        if hit:
            self.probe.append({'before':before,'returned':bool(ok),'submitDelta':int(self.submits)-s0,'nDelta':int(self.n)-n0,
                               'newR263Events':[x for x in self.r263Events if int(x.get('t') or -1)==int(t)]})
        return ok

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_r263_probe_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=Probe(tmp/f'{m}.json.xz',m,1,4)
            try:r=s.run_r264(co[m]['winner'])
            finally:s.close()
            row={'marketId':m,'probe':s.probe,'correct':bool(r.get('r264CorrectnessPass'))};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R264_R263_TRIGGER_PARITY_PROBE_V1','researchOnly':True,'behaviorMutation':False,'rows':rows,
             'gates':{'correctnessPass':all(x['correct'] for x in rows),'targetCallObserved2of2':all(len(x['probe'])==1 for x in rows),
                      'ordinaryR263Submitted2of2':all(len(x['probe'])==1 and x['probe'][0]['returned'] and x['probe'][0]['submitDelta']==1 for x in rows)},
             'boundary':['consumed only','exact prereg trigger timestamps','R2.64 behavior unchanged','winner only run scoring, not probe logic','no fresh/no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
