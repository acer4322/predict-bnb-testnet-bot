from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1; v2=r1.v2; EPS=1e-9

class EconomicOpportunityShadowSim(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots); self.ecoShadow=[]
    def _fifo_state_from_history(self):
        un={'UP':[],'DOWN':[]};reserve=debt=0.0
        for e in self.slot_history:
            if e.get('event')!='ROLE_FILL_SPLIT':continue
            q=float(e.get('fillInc') or 0.0);p=float(e.get('price') or 0.0);side=str(e.get('side'));opp='DOWN' if side=='UP' else 'UP';rem=q
            while rem>EPS and un[opp]:
                lot=un[opp][0];m=min(rem,lot['qty']);ps=lot['price']+p
                reserve+=m*max(0.0,1.0-ps);debt+=m*max(0.0,ps-1.0);rem-=m;lot['qty']-=m
                if lot['qty']<=EPS:un[opp].pop(0)
            if rem>EPS:un[side].append({'qty':rem,'price':p})
        return un,reserve,debt
    @staticmethod
    def _hypo(un,side,p,q):
        opp='DOWN' if side=='UP' else 'UP';lots=[dict(x) for x in un[opp]];rem=float(q);paired=good=bad=0.0;cost=0.0
        while rem>EPS and lots:
            lot=lots[0];m=min(rem,lot['qty']);ps=float(lot['price'])+float(p);paired+=m;cost+=m*ps;good+=m*max(0.0,1.0-ps);bad+=m*max(0.0,ps-1.0);rem-=m;lot['qty']-=m
            if lot['qty']<=EPS:lots.pop(0)
        return {'pairedQty':paired,'weightedPairSum':cost/paired if paired>EPS else None,'reserveGain':good,'pairDamage':bad,'unpairedQty':max(0.0,q-paired)}
    def _shadow(self,t,side,role,p,q,kind):
        if role not in {'SATELLITE_REPAIR','ECONOMIC_CORE'}:return
        un,reserve,debt=self._fifo_state_from_history(); actual=self._hypo(un,side,p,q)
        levels=[]
        try: raw_levels=self._live_price_levels(side)
        except Exception: raw_levels=[]
        avail=max(0.0,float(self._scope_debt_qty())-float(self._reserved_repair_quota(side))) if self.scopeSide is not None else 0.0
        for rank,raw in enumerate(raw_levels,1):
            pp=float(v2.kprice(raw))
            if pp<=EPS:continue
            qq=1.0/pp
            if not math.isfinite(qq) or qq<=EPS or qq>12.0+EPS:continue
            h=self._hypo(un,side,pp,qq)
            if h['unpairedQty']>EPS or qq>avail+EPS:continue
            levels.append({'rank':rank,'price':pp,'qty':qq,**h})
        best=min(levels,key=lambda x:(x['pairDamage'],-x['reserveGain'],x['rank'])) if levels else None
        self.ecoShadow.append({'t':int(t),'kind':kind,'role':role,'side':side,'submitPrice':float(p),'submitQty':float(q),'scopeGeneration':int(self.scopeGeneration),'repairProgressClock':int(self.scopeRepairProgressClocks),'scopeDebtQty':float(self._scope_debt_qty()),'reservedRepairQuota':float(self._reserved_repair_quota(side)),'availableRepairQty':avail,'realizedPairReserve':reserve,'realizedPairDebt':debt,'realizedNetPairEdge':reserve-debt,'actual':actual,'bestPassiveAlternative':best,'passiveAlternatives':levels[:12]})
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        self._shadow(t,side,role,p,q,'PASSIVE_SUBMIT')
        return super()._submit_role_v8(t,side,role,p,q,proj,split)
    def _submit_active(self,t,side,role,q,overflow_risk,bookinfo):
        qv=v2.base.quotes(self.book);p=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else float('nan')
        if math.isfinite(p):self._shadow(t,side,role,p,q,'ACTIVE_SUBMIT')
        return super()._submit_active(t,side,role,q,overflow_risk,bookinfo)
    def run_shadow(self,w):
        r=super().run_cap(w);r['economicOpportunityShadow']=self.ecoShadow[:3000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r28_eco_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];s=EconomicOpportunityShadowSim(tmp/'tapes'/f'{mid}.json.xz',4)
            try:r=s.run_shadow(cr['winner'])
            finally:s.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r})
            sh=r['economicOpportunityShadow'];acts=[x for x in sh if x['kind']=='ACTIVE_SUBMIT'];sats=[x for x in sh if x['kind']=='PASSIVE_SUBMIT' and x['role']=='SATELLITE_REPAIR']
            print(json.dumps({'marketId':mid,'submits':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'shadowRows':len(sh),'activeRows':len(acts),'satRows':len(sats),'activeMeanDamage':sum(x['actual']['pairDamage'] for x in acts)/len(acts) if acts else None,'activeAltAvailable':sum(1 for x in acts if x['bestPassiveAlternative'] is not None),'satMeanDamage':sum(x['actual']['pairDamage'] for x in sats)/len(sats) if sats else None,'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        out={'version':'MS4_R28_ECONOMIC_OPPORTUNITY_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'boundary':['R2.8 CAP1 behavior frozen/instrumentation only','FIFO realized pair reserve/debt reconstructed strictly from prior confirmed fills','candidate pairDamage computed against current FIFO unmatched opposite lots','passive alternatives are current live-book venue-min pure-repair quantities that fit currently unreserved Repair debt','no admission/routing/price/qty change','no Target runtime input','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(rows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
