from __future__ import annotations
import argparse, json, math, os, shutil, tempfile, zipfile, sys
from pathlib import Path
import numpy as np, joblib

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED))
    import run_lane_g_multi_action_exact_fork_v1b as ma
    import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma
    from tools import train_lane_g_r264_execution_world_v1 as wm

EPS=ma.EPS


def clamp01(x):
    return max(0.0,min(1.0,float(x)))

def race_prob(p_branch,p_sibling,h=5.0):
    pb=clamp01(p_branch); ps=clamp01(p_sibling)
    lb=-math.log(max(1e-9,1.0-pb))/h if pb<1.0 else 50.0
    ls=-math.log(max(1e-9,1.0-ps))/h if ps<1.0 else 50.0
    if lb+ls<=EPS:return 0.5
    return float(lb/(lb+ls))

class HazardSnapshotSim(ma.MultiActionExactForkSim):
    def __init__(self,tape,market_id,world_bundle):
        super().__init__(tape,market_id,'KEEP_REPAIR',1,4)
        self.world_bundle=world_bundle
        self._end_ms=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        self.hazardSnapshot=None

    def _predict(self,row):
        xx=wm.X([row],True)
        out={}
        for label in ('anyFill3s','cancelReq3s','fillQty3s','repairPayQty3s','anyFill5s','cancelReq5s','fillQty5s','repairPayQty5s'):
            model=self.world_bundle['models'].get((label,'stateAction'))
            if model is None:continue
            if hasattr(model,'predict_proba'):
                out[label]=clamp01(model.predict_proba(xx)[0,1])
            else:
                out[label]=max(0.0,float(model.predict(xx)[0]))
        return out

    def _row(self,t,side,role,price,qty,key,source):
        return wm.TraceSim._state_row(self,t,side,role,price,qty,'PASSIVE',key,source)

    def _trigger_snapshot(self,t):
        base=super()._trigger_snapshot(t)
        s=self.spec
        sib_key=str(s['siblingKey']); sib_o=self.orders.get(sib_key) or {}
        sib_side=str(s['repairSide']); sib_role=str(self.key_role.get(sib_key) or 'SATELLITE_REPAIR')
        try:sib_qty=float(self._remaining(sib_key))
        except Exception:sib_qty=float(s['siblingRemaining'])
        sib_price=float(sib_o.get('price') or s['siblingPrice'])
        rows={
            'KEEP_REPAIR':self._row(t,sib_side,sib_role,sib_price,sib_qty,sib_key,'EXISTING_REPAIR_SIBLING'),
            'ORDINARY_REEXPAND':self._row(t,str(s['ordinary']['side']),'SATELLITE_EXPAND',float(s['ordinary']['price']),float(s['ordinary']['qty']),'HYPOTHETICAL_ORDINARY','HYPOTHETICAL_ORDINARY'),
            'R303_CONTINGENT_COMPOSITE':self._row(t,str(s['composite']['side']),'SATELLITE_REPAIR',float(s['composite']['price']),float(s['composite']['qty']),'HYPOTHETICAL_R303','HYPOTHETICAL_R303'),
        }
        preds={k:self._predict(v) for k,v in rows.items()}
        p_sib=preds['KEEP_REPAIR'].get('anyFill5s',0.0)
        race={
            'ORDINARY_REEXPAND':race_prob(preds['ORDINARY_REEXPAND'].get('anyFill5s',0.0),p_sib),
            'R303_CONTINGENT_COMPOSITE':race_prob(preds['R303_CONTINGENT_COMPOSITE'].get('anyFill5s',0.0),p_sib),
        }
        self.hazardSnapshot={'t':int(t),'stateRows':rows,'predictions':preds,'raceVsSibling5s':race}
        return base

    def run_hazard(self,winner):
        r=super().run_exact(winner)
        r['hazardSnapshot']=self.hazardSnapshot
        return r

def load_feature_ranges(path):
    mins={};maxs={};n=0
    if not path:return {'n':0,'mins':{},'maxs':{}}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip():continue
        r=json.loads(line);n+=1
        for k in wm.STATE+wm.ACTION:
            try:v=float(r.get(k,0.0))
            except Exception:continue
            if not math.isfinite(v):continue
            mins[k]=v if k not in mins else min(mins[k],v);maxs[k]=v if k not in maxs else max(maxs[k],v)
    return {'n':n,'mins':mins,'maxs':maxs}

def ood(row,ranges):
    out=[]
    for k in wm.STATE+wm.ACTION:
        if k not in ranges['mins']:continue
        try:v=float(row.get(k,0.0))
        except Exception:continue
        lo=float(ranges['mins'][k]);hi=float(ranges['maxs'][k])
        if v<lo-EPS or v>hi+EPS:out.append({'feature':k,'value':v,'trainMin':lo,'trainMax':hi})
    return out

def actual_branch_first(branch,sibling_key):
    ev=(branch or {}).get('firstStructuralEvent') or {}
    reasons=ev.get('reasons') or []
    branch_key=(branch or {}).get('branchKey')
    fill_keys=[str(x.get('key')) for x in reasons if x.get('type')=='CONFIRMED_INVOLVED_FILL']
    if branch_key and str(branch_key) in fill_keys:return 1
    if str(sibling_key) in fill_keys:return 0
    return None

def actual_keep_fill5(mdoc):
    b=mdoc['branches']['KEEP_REPAIR'];ev=b.get('firstStructuralEvent') or {};t0=int(mdoc['spec']['t']);te=ev.get('t')
    if te is None or int(te)>t0+5000:return 0
    sib=str(mdoc['spec']['siblingKey'])
    return 1 if any(x.get('type')=='CONFIRMED_INVOLVED_FILL' and str(x.get('key'))==sib for x in (ev.get('reasons') or [])) else 0

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--anchors',required=True);ap.add_argument('--world-model',required=True);ap.add_argument('--training-rows',default='');ap.add_argument('--output',required=True);a=ap.parse_args()
    anchors=json.loads(Path(a.anchors).read_text(encoding='utf-8'));specs={int(k):v['spec'] for k,v in anchors['markets'].items()};mids=sorted(specs)
    for m,s in specs.items():ma.FROZEN[m]=s
    world=joblib.load(a.world_model);ranges=load_feature_ranges(a.training_rows)
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_hazard_anchor_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];race_rows=[];keep_rows=[]
        for m in mids:
            sim=HazardSnapshotSim(tmp/f'{m}.json.xz',m,world)
            try:r=sim.run_hazard(co[m]['winner'])
            finally:sim.close()
            hs=r.get('hazardSnapshot') or {};mdoc=anchors['markets'][str(m)]
            ood_by={k:ood(v,ranges) for k,v in (hs.get('stateRows') or {}).items()}
            row={'marketId':m,'triggered':r['triggered'],'correctRaw':r['correct'],'triggerParityErrors':r['triggerParityErrors'],'predictions':hs.get('predictions'),'raceVsSibling5s':hs.get('raceVsSibling5s'),'outOfRangeByAction':ood_by,'worldStateRows':hs.get('stateRows')}
            rows.append(row)
            keep_actual=actual_keep_fill5(mdoc);keep_pred=float(hs['predictions']['KEEP_REPAIR']['anyFill5s']);keep_rows.append({'marketId':m,'pred':keep_pred,'actual':keep_actual,'brier':(keep_pred-keep_actual)**2})
            for action in ('ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE'):
                actual=actual_branch_first(mdoc['branches'][action],mdoc['spec']['siblingKey']);pred=float(hs['raceVsSibling5s'][action])
                if actual is not None:race_rows.append({'marketId':m,'action':action,'predBranchFirst':pred,'actualBranchFirst':actual,'brier':(pred-actual)**2,'correctClass':int((pred>=0.5)==bool(actual))})
            print(json.dumps({'marketId':m,'pKeepFill5':keep_pred,'pOrdFill5':hs['predictions']['ORDINARY_REEXPAND']['anyFill5s'],'pR303Fill5':hs['predictions']['R303_CONTINGENT_COMPOSITE']['anyFill5s'],'race':hs['raceVsSibling5s'],'oodCounts':{k:len(v) for k,v in ood_by.items()}},ensure_ascii=False),flush=True)
        race_brier=float(np.mean([x['brier'] for x in race_rows])) if race_rows else None;race_acc=float(np.mean([x['correctClass'] for x in race_rows])) if race_rows else None;keep_brier=float(np.mean([x['brier'] for x in keep_rows])) if keep_rows else None
        gates={'allTriggered':all(x['triggered'] for x in rows),'triggerParityClean':all(not x['triggerParityErrors'] for x in rows),'worldPredictionsFinite':all(all(math.isfinite(float(v)) for p in x['predictions'].values() for v in p.values()) for x in rows),'noStateFeatureOODForAllActions':all(all(len(v)==0 for v in x['outOfRangeByAction'].values()) for x in rows)}
        out={'version':'LANE_G_MICROWORLD_V2_REAL_HAZARD_ANCHOR_BRIDGE_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'calibration':{'keepFill5':keep_rows,'keepFill5Brier':keep_brier,'raceEligible':race_rows,'raceBrier':race_brier,'raceAccuracyAt05':race_acc},'featureRangesSourceRows':ranges['n'],'gates':gates,'boundary':['strict-past exact R2.64 replay to frozen H100 trigger only','world model trained on chronological 70/30 consumed H100 carrier outcomes','KEEP hazard evaluated on existing Repair sibling','R303 fillability evaluated as SATELLITE_REPAIR carrier geometry; composite accounting remains deterministic outside world model','race probability derives from independent exponential hazards from 5s fill probabilities; this is an explicit bridge assumption','no future branch outcome used in model features','first structural event used only for calibration','no fresh/no dream fill/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'gates':gates,'calibration':out['calibration']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
