from __future__ import annotations
import argparse,json,os,shutil,sys,tempfile,zipfile,collections
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
HBT244=Path(__file__).resolve().parent/'hftbacktest_244'
if HBT244.exists() and str(HBT244) not in sys.path:sys.path.insert(0,str(HBT244))
import importlib.util
def _sib(name,filename):
 p=Path(__file__).resolve().with_name(filename); sp=importlib.util.spec_from_file_location(name,p)
 if sp is None or sp.loader is None: raise ImportError(p)
 m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m); return m
s0=_sib('struct_s0','run_eth_parent_occupancy_anchorless_parallel_ab.py')
s1=_sib('struct_s1','run_eth_parent_occupancy_passive_evidence_ab.py')
s2=_sib('struct_s2','run_eth_parent_occupancy_transition_frontier_ab.py')
s3=_sib('struct_s3','run_eth_parent_occupancy_prospective_guard_ab.py')
EPS=1e-9
MIDS=[1945623,1945986]
CELLS=[('S0_PARENT_OCCUPANCY_ANCHORLESS',s0.ParentOccupancyAnchorlessParallelHFT,'run_parent_occupancy'),('S1_ADD_PASSIVE_EVIDENCE',s1.PassiveEvidenceParentOccupancyHFT,'run_passive_evidence'),('S2_ADD_TRANSITION_FRONTIER',s2.TransitionFrontierParentOccupancyHFT,'run_transition_frontier'),('S3_ADD_PROSPECTIVE_GUARD',s3.ProspectiveGuardParentOccupancyHFT,'run_guard')]
def row(label,mid,r):
 ss=s1.safety(r)
 return {'cell':label,'marketId':mid,'submits':int(r.get('submits') or 0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'pnl':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'activeRepairFillQty':float(r.get('parallelRepairActiveFillQty') or 0),'anchorlessActiveSubmits':int(r.get('anchorlessActiveSubmits') or 0),'passiveWait':int(r.get('anchorlessWaitPassiveEvidence') or 0),'transitionBlocks':int(r.get('transitionBlocks') or 0),'prospectiveBlocks':int(r.get('prospectiveTransitionBlocks') or 0),'truthMismatch':float(ss.get('truthMismatch') or 0),'safetyZero':all(float(v or 0)<=EPS for v in ss.values()),'safety':ss}
def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True);a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='struct_guard_reintro_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=s1.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   tape=tmp/'tapes'/f'{mid}.json.xz';cr=co[mid]
   for label,cls,method in CELLS:
    sim=s1.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
    try:r=getattr(sim,method)(models,cr['winner'])
    finally:sim.close()
    x=row(label,mid,r);rows.append(x);print(json.dumps({'progress':label,'market':mid,'submits':x['submits'],'fills':x['fills'],'rounds':x['rounds'],'pnl':x['pnl'],'floor':x['floor'],'wait':x['passiveWait'],'tb':x['transitionBlocks'],'pb':x['prospectiveBlocks'],'truth':x['truthMismatch']},ensure_ascii=False),flush=True)
  out={'version':'ETH_STRUCTURAL_GUARD_REINTRODUCTION_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketIds':mids,'rows':rows,'boundary':['cumulative exact structural guard modules','EconomicManagementV1 profile fixed','realistic HFT','no tuning','no Target runtime input','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':rows},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
