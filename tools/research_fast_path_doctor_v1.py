from __future__ import annotations
import argparse,json,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.research_data_access_v1 import DEFAULT_CAPSULE,DEFAULT_OUR_CAPSULE,DEFAULT_PHASEB_CAPSULE
from tools.execution_research_cache_registry_v1 import check_registry as check_execution_cache
from tools.build_execution_bundle_market_registry_v1 import check as check_bundle_registry

EXEC_ROOT=ROOT/'data'/'research'/'management_training_v1'/'execution_research_cache_v1'
BUNDLE_ROOT=ROOT/'data'/'research'/'execution_bundle_market_registry_v1'
REQUIRED={
 'target_features':Path(DEFAULT_CAPSULE)/'decision_features_v1.parquet',
 'target_parents':Path(DEFAULT_CAPSULE)/'target_parents.parquet',
 'research_market_registry':Path(DEFAULT_CAPSULE).parent/'research_market_registry_v1.parquet',
 'our_features':Path(DEFAULT_OUR_CAPSULE)/'our_decision_features_v1.parquet',
 'phaseb_teacher':Path(DEFAULT_PHASEB_CAPSULE)/'phaseb_counterfactual_teacher_v1.parquet',
 'execution_cache_registry':EXEC_ROOT/'execution_research_cache_registry_v1.json',
 'bundle_market_registry':BUNDLE_ROOT/'bundle_market_registry_v1.parquet',
}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--deep',action='store_true');a=ap.parse_args();t0=time.perf_counter()
 files={k:{'exists':p.exists(),'bytes':p.stat().st_size if p.exists() else None} for k,p in REQUIRED.items()}
 ex=check_execution_cache(deep=bool(a.deep))
 br=check_bundle_registry(BUNDLE_ROOT)
 ready=all(x['exists'] for x in files.values()) and bool(ex.get('all_fresh')) and bool(br.get('fresh'))
 out={'version':'BTC5M_RESEARCH_FAST_PATH_DOCTOR_V1','ready':ready,'deep':bool(a.deep),'files':files,'executionCacheFresh':bool(ex.get('all_fresh')),'bundleRegistryFresh':bool(br.get('fresh')),'executionCache':ex,'bundleRegistry':br,'elapsedMs':round((time.perf_counter()-t0)*1000,4),'boundary':['lightweight startup health check','no large SQLite scan','no ZIP central-directory refresh','no tape decompression','deep only re-verifies execution-cache hashes when requested']}
 print(json.dumps(out,indent=2,ensure_ascii=False));raise SystemExit(0 if ready else 2)
if __name__=='__main__':main()
