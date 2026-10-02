from __future__ import annotations
import argparse,copy,json,os,sys,tempfile,zipfile
from pathlib import Path
try:
    import tools.run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1
except ModuleNotFoundError:
    sys.path.insert(0,str(Path.cwd().parent))
    import run_v3b_depth_vs_price_direction_one_shot_smoke3 as v1

MARKETS=[1823614,1823769,1823886,1823894,1823907,1824031,1824034,1824037,1824045,1824293,1824296,1824301,1824401]

class Discover(v1.DirectionOneShot):
    def __init__(self,tape):
        super().__init__(tape,'CONTROL',None); self.candidates=[]
    def _open_one_option(self,t,qv,end):
        q=self._qualifies(qv)
        if q is not None:
            snap,dig=self._snapshot_prefix(t); before=len(self.slot_history)
            out=v1.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
            ev=[x for x in self.slot_history[before:] if x.get('event')=='ROLE_SLOT_SUBMIT']
            hit=next((x for x in ev if str(x.get('role'))=='SATELLITE_EXPAND' and str(x.get('side'))==q['dominant']),None)
            if hit is not None:
                self.candidates.append({'t':int(t),'prefixDigest':dig,**q,'submit':v1.norm(hit)})
            return out
        return v1.v3b.FifoAggregateResponsibilityLadderV3B._open_one_option(self,t,qv,end)
    def run_discovery(self):
        r=super(v1.DirectionOneShot,self).run_qty('__UNSCORED__')
        return r

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    rows=[]
    with tempfile.TemporaryDirectory(prefix='v3b_conflict_discovery_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in MARKETS:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(MARKETS,1):
            sim=Discover(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_discovery(); cands=list(sim.candidates)
            finally:sim.close()
            fills={}
            fill_times={}
            for f in r.get('fillSideSequence',[]):
                k=str(f.get('key')); fills[k]=fills.get(k,0.0)+float(f.get('incQty') or 0.0); fill_times.setdefault(k,int(f.get('t')))
            chosen=None
            for c in cands:
                k=str(c['submit']['key']); fq=float(fills.get(k,0.0))
                if fq>1e-9:
                    chosen={'submitT':int(c['t']),'side':str(c['submit']['side']),'price':float(c['submit']['price']),'qty':float(c['submit']['qty']),
                            'baselineFillT':int(fill_times[k]),'baselineFillQty':fq,'dominant':c['dominant'],'dominantMid':float(c['dominantMid']),
                            'depthDirection':c['depthDirection'],'prefixDigest':c['prefixDigest'],'key':k}
                    break
            row={'marketId':mid,'candidateCount':len(cands),'filledCandidateCount':sum(float(fills.get(str(c['submit']['key']),0.0))>1e-9 for c in cands),'chosen':chosen}
            rows.append(row); print(json.dumps({'progress':i,'of':len(MARKETS),**row},ensure_ascii=False),flush=True)
    out={'version':'V3B_DEPTH_PRICE_FILLED_CONFLICT_STAGEA13_BASELINE_DISCOVERY','date':'2026-09-07','researchOnly':True,'treatmentOutcomeUsed':False,
         'markets':MARKETS,'rows':rows,'allMarketsHaveChosen':all(r['chosen'] is not None for r in rows),
         'boundary':['baseline V3B only','candidate requires last physical fill class REPAIR','baseline decision SATELLITE_EXPAND on dominant side','depth direction supports dominant side','dominant midpoint <0.5','chosen carrier must later physically fill in baseline','first such filled carrier per market','no winner/PnL used in selection','NEW24-B untouched']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps({'ok':True,'allMarketsHaveChosen':out['allMarketsHaveChosen']},ensure_ascii=False))
if __name__=='__main__':main()
