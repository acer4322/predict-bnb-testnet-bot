from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
EPS=1e-9

class RearmRepairFrontierAudit(r278.PersistentIntentThesisCycleCapitalSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.r279=[]
    def _try_cycle_rearm(self,t,end):
        n0=int(self.r278Stats.get('REARM_SUBMIT',0));ok=super()._try_cycle_rearm(t,end)
        if ok and int(self.r278Stats.get('REARM_SUBMIT',0))>n0:
            ev=next((x for x in reversed(self.r278Events) if x.get('event')=='R278_INTENT_THESIS_CYCLE_REARM_SUBMIT'),None)
            if ev:
                rs='DOWN' if ev['thesisSide']=='UP' else 'UP';levels=[]
                for rank,raw in enumerate(self._live_price_levels(rs),1):
                    p=float(r278.v2.kprice(raw))
                    if p<=EPS:continue
                    q=1.0/p
                    if q<=EPS or q>12+EPS:continue
                    levels.append({'rank':rank,'price':p,'qty':q,'pairSum':p+float(ev['price'])})
                self.r279.append({'t':int(t),'key':ev['key'],'riskSide':ev['thesisSide'],'riskPrice':float(ev['price']),
                    'repairSide':rs,'bestRepairLevels':levels[:8],'rank1PairSum':(levels[0]['pairSum'] if levels else None),
                    'anyPairLe1':any(x['pairSum']<=1+EPS for x in levels),'bestPairSum':min((x['pairSum'] for x in levels),default=None)})
        return ok
    def run_audit(self,w):
        r=super().run_r278(w);r['r279Frontier']=self.r279;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r279_'))
    try:
      with zipfile.ZipFile(a.bundle) as z:
       co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
       for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
      rows=[]
      for m in mids:
       s=RearmRepairFrontierAudit(tmp/f'{m}.json.xz',1,4)
       try:r=s.run_audit(co[m]['winner'])
       finally:s.close()
       x={'marketId':m,'winnerPostHocOnly':co[m]['winner'],'pnl':r['pnlDiagnosticOnly'],'best':r['best'],'floor':r['floor'],
          'rearmSubmits':r.get('r278RearmSubmits'),'rearmFills':r.get('r278RearmFills'),'frontier':r.get('r279Frontier') or []}
       rows.append(x);print(json.dumps(x,ensure_ascii=False),flush=True)
      out={'version':'MS4_R2_79_R278_REARM_REPAIR_FRONTIER_AUDIT_V1','researchOnly':True,'behaviorChange':False,
           'rows':rows,'boundary':['exact R278 behavior plus telemetry only','repair frontier is strict-past live Maker levels at R278 rearm submit','no winner/future runtime input']}
      Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
