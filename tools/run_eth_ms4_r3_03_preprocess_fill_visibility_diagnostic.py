from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9

class PreprocessFillVisibilityDiagnostic(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.r303diag=[];self.r303Mismatch=0;self.r303VisibleFillEvents=0

    def process(self,t):
        pre={}
        for key,o in self.orders.items():
            try:s=self.snap(o);cur=float(s.get('cumExecQty') or 0.0)
            except Exception:continue
            stored=float(o.get('cum') or 0.0);inc=max(0.0,cur-stored)
            if inc>EPS:pre[str(key)]=inc
        start=len(self.splitEvents)
        super().process(t)
        actual={}
        for ev in self.splitEvents[start:]:
            if ev.get('event')=='ROLE_FILL_SPLIT' and float(ev.get('fillInc') or 0)>EPS:
                actual[str(ev.get('key'))]=actual.get(str(ev.get('key')),0.0)+float(ev.get('fillInc') or 0.0)
        keys=set(pre)|set(actual)
        if keys:
            self.r303VisibleFillEvents+=len(keys)
            for k in sorted(keys):
                p=float(pre.get(k,0.0));a=float(actual.get(k,0.0));ok=abs(p-a)<=1e-9
                if not ok:self.r303Mismatch+=1
                self.r303diag.append({'t':int(t),'key':k,'preVisibleFillInc':p,'actualRoleFillSplitInc':a,'match':ok})

    def run_diag(self,winner):
        r=super().run_r264(winner)
        r.update({'r303PreprocessVisibilityEvents':self.r303diag[:5000],
                  'r303PreprocessVisibleFillKeys':int(self.r303VisibleFillEvents),
                  'r303PreprocessMismatch':int(self.r303Mismatch),
                  'r303PreprocessVisibilityPass':self.r303Mismatch==0 and self.r303VisibleFillEvents>0})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r303_previs_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=PreprocessFillVisibilityDiagnostic(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_diag(co[m]['winner'])
            finally:s.close()
            row={'marketId':m,'visibleFillKeys':r['r303PreprocessVisibleFillKeys'],'mismatch':r['r303PreprocessMismatch'],
                 'pass':bool(r['r303PreprocessVisibilityPass']),'correct':bool(r.get('r264CorrectnessPass')),
                 'events':r['r303PreprocessVisibilityEvents']}
            rows.append(row);print(json.dumps({k:row[k] for k in ['marketId','visibleFillKeys','mismatch','pass','correct']},ensure_ascii=False),flush=True)
        out={'version':'MS4_R303_PREPROCESS_FILL_VISIBILITY_DIAGNOSTIC_V1','researchOnly':True,'behaviorChange':False,'markets':mids,'rows':rows,
             'gates':{'allVisibilityMatch':all(x['pass'] for x in rows),'correctnessPass':all(x['correct'] for x in rows)},
             'boundary':['R2.64 exact behavior unchanged','exchange already advanced before process','snap cumExecQty compared with stored local cum before base process','actual label is subsequent ROLE_FILL_SPLIT same receipt','no Target/winner/future runtime input','consumed HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
