from __future__ import annotations
import argparse,copy,json,math,os,sys,tempfile,zipfile,shutil
from pathlib import Path
import joblib

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_r263_r303_same_authority_scan_v1.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists() and (STAGED/'evaluate_lane_g_single_event_counterfactual_engine_v1.py').exists():
    sys.path.insert(0,str(STAGED))
    import run_lane_g_r263_r303_same_authority_scan_v1 as scanmod
    import train_lane_g_r264_execution_world_v1 as wm
    import evaluate_lane_g_single_event_counterfactual_engine_v1 as eng
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_r263_r303_same_authority_scan_v1 as scanmod
    from tools import train_lane_g_r264_execution_world_v1 as wm
    from tools import evaluate_lane_g_single_event_counterfactual_engine_v1 as eng

EPS=1e-9

def payoff(sim):
    u=float(sim.inv['UP']);d=float(sim.inv['DOWN']);c=float(sim.cost);b=max(u,d)-c;f=min(u,d)-c
    return {'upQty':u,'downQty':d,'cost':c,'best':b,'floor':f,'gap':b-f}

def live_row(sim,t,key,source):
    o=sim.orders.get(str(key))
    if not o:return None
    try:rem=float(sim.snap(o).get('leavesQty'))
    except Exception:rem=float(sim._remaining(str(key)))
    return wm.TraceSim._state_row(sim,int(t),str(o['side']),str(sim.key_role.get(str(key)) or 'SATELLITE_REPAIR'),float(o['price']),max(0.0,rem),'PASSIVE',str(key),source)

def guard(sim):
    return {'inv':(float(sim.inv['UP']),float(sim.inv['DOWN'])),'cost':float(sim.cost),'submits':int(sim.submits),'n':int(sim.n),'slotKey':tuple(sorted((int(k),str(v)) for k,v in sim.slot_key.items())),'activeKeys':tuple(sorted(str(x) for x in sim.activeKeys)),'r263Used':tuple(sorted(int(x) for x in sim.r263GenerationUsed)),'riskConsumed':float(getattr(sim,'scopeRiskCreditConsumed',0.0))}

class ShadowAdvisor(scanmod.SameAuthorityScan):
    def __init__(self,tape,model,*a,**kw):
        super().__init__(tape,*a,**kw);self.model=model;self.advisor=[];self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _try_r263(self,t,end):
        pre=self._r303_structural_pre(t,end);base=payoff(self);keep_rows={}
        if pre.get('ok'):
            sk=str(pre['siblingKey']);sr=live_row(self,t,sk,'SHADOW_KEEP_SIBLING_PRECALL')
            if sr:keep_rows[sk]=sr
        existing=set(str(k) for k in self.orders);sub0=int(self.submits)
        ok=super()._try_r263(t,end)
        if not (pre.get('ok') and ok and int(self.submits)-sub0==1):return ok
        newkeys=[k for k in self.orders if str(k) not in existing];branch_key=str(newkeys[-1]) if newkeys else None
        if not branch_key:return ok
        sk=str(pre['siblingKey'])
        ordinary_rows={}
        sr=live_row(self,t,sk,'SHADOW_ORDINARY_SIBLING_POSTSUBMIT');br=live_row(self,t,branch_key,'SHADOW_ORDINARY_BRANCH_POSTSUBMIT')
        if sr:ordinary_rows[sk]=sr
        if br:ordinary_rows[branch_key]=br
        # Exact-anchor-derived constructor for hypothetical R303 post-submit state.
        # Native Ordinary and R303 consume the same one-unit authority; physical inventory/cost do not change on submit.
        r303_rows={}
        hs=live_row(self,t,sk,'SHADOW_R303_SIBLING_HYPOTHETICAL_POSTSUBMIT')
        cp=float(pre['compositePrice']);cq=float(pre['compositePhysicalQty']);side=str(pre['thesisSide'])
        hb=wm.TraceSim._state_row(self,int(t),side,'SATELLITE_REPAIR',cp,cq,'PASSIVE','__R303_HYPOTHETICAL__','SHADOW_R303_BRANCH_HYPOTHETICAL_POSTSUBMIT')
        for rr in [hs,hb]:
            if rr is None:continue
            rr['repairLiveSlots']=float(rr['repairLiveSlots'])+1.0
            rr['expandLiveSlots']=max(0.0,float(rr['expandLiveSlots'])-1.0)
        if hs:r303_rows[sk]=hs
        if hb:r303_rows['__R303_HYPOTHETICAL__']=hb
        g0=guard(self)
        pred_keep=eng.engine(base,keep_rows,self.model) if keep_rows else None
        pred_ord=eng.engine(base,ordinary_rows,self.model) if ordinary_rows else None
        pred_r303=eng.engine(base,r303_rows,self.model) if r303_rows else None
        g1=guard(self);mutation=(g0!=g1)
        self.advisor.append({'t':int(t),'generation':int(pre['generation']),'basePayoff':base,'pre':pre,'ordinaryBranchKey':branch_key,'predictions':{'KEEP_REPAIR':pred_keep,'ORDINARY_REEXPAND':pred_ord,'R303_CONTINGENT_COMPOSITE':pred_r303},'mutationDetected':mutation,'guardBefore':g0,'guardAfter':g1})
        return ok

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='lane_g_shadow_advisor_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];diag=[]
        for i,m in enumerate(mids,1):
            s=ShadowAdvisor(tmp/f'{m}.json.xz',model,1,4)
            try:r=s.run_scan(co[m]['winner'])
            finally:s.close()
            for x in s.advisor:rows.append({'marketId':m,**x})
            d={'marketId':m,'advisorCount':len(s.advisor),'mutationCount':sum(1 for x in s.advisor if x['mutationDetected']),'correct':bool(r.get('r264CorrectnessPass'))};diag.append(d);print(json.dumps({'progress':i,'of':len(mids),**d},ensure_ascii=False),flush=True)
        # Report vector spread only; no scalar policy winner is created.
        spreads=[]
        for x in rows:
            pp=x['predictions'];vals={a:(pp[a] or {}).get('expected') for a in ['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']}
            if all(v is not None for v in vals.values()):
                fs=[vals[a]['dFloor'] for a in vals];bs=[vals[a]['dBest'] for a in vals];gs=[vals[a]['dGap'] for a in vals]
                spreads.append({'marketId':x['marketId'],'t':x['t'],'floorRange':max(fs)-min(fs),'bestRange':max(bs)-min(bs),'gapRange':max(gs)-min(gs),'vectors':vals})
        summary={'markets':len(mids),'advisorStates':len(rows),'marketsWithAdvisor':len(set(x['marketId'] for x in rows)),'mutationCount':sum(1 for x in rows if x['mutationDetected']),'meanFloorRange':sum(x['floorRange'] for x in spreads)/len(spreads) if spreads else None,'meanBestRange':sum(x['bestRange'] for x in spreads)/len(spreads) if spreads else None,'meanGapRange':sum(x['gapRange'] for x in spreads)/len(spreads) if spreads else None}
        gates={'allBaselineCorrect':all(x['correct'] for x in diag),'noAdvisorMutation':summary['mutationCount']==0,'allAdvisorVectorsPresent':all(all((x['predictions'].get(a) or {}).get('expected') is not None for a in ['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']) for x in rows),'advisorFoundIfAnySameAuthority':True}
        out={'version':'LANE_G_SINGLE_EVENT_SHADOW_ADVISOR_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'marketDiagnostics':diag,'rows':rows,'spreads':spreads,'summary':summary,'gates':gates,'shadowPass':all(gates.values()),'boundary':['whole episode remains historical exact HFT','single-event engine invoked only at naturally reached same-authority R2.63/R3.03 seam','advisor output is vector-valued and shadow-only','baseline native Ordinary action remains unchanged','no scalar reward/no runtime authority','hypothetical R303 post-submit state uses only exact-anchor-proven occupancy delta: same one-unit risk debt, +1 live slot, repair slot instead of expand slot','no multi-step synthetic market rollout','consumed only/no fresh/no winner future/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'summary':summary,'gates':gates,'shadowPass':out['shadowPass'],'spreads':spreads[:20]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
