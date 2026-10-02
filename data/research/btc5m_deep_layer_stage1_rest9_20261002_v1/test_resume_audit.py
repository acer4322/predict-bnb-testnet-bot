"""Regression against the real completed first path; no replay or native import."""
import hashlib,json,sys
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[2];sys.path.insert(0,str(P))
from deep_audit import audit
def run():
 parent=P.parent/'btc5m_deep_layer_stage1_20261002_v1'
 assert (P/'deep_layer.py').read_bytes()==(parent/'deep_layer.py').read_bytes()
 for n in ('sizing.py','overlay/run_variant.py','passive_offset.py','risk_floor.py','governor.py','hard_stop.py'):
  assert (P/n).read_bytes()==(parent/n).read_bytes(),n
 first=ROOT/'data/research/lan_worker_returns/btc5m-deep-layer-stage1-20261002-v1/arms/deep1_DEEP_2671717'
 base=ROOT/'data/research/lan_worker_returns/btc5m-cg1at-fresh100a-20260930/arms/c100_CG1AT_2671717'
 x=audit(first,base);assert x['status']=='PASS' and all(x['mandatory'].values()) and x['deep_orders']==51
 assert json.loads((first/'AUDIT.json').read_text())['status']=='PATH_ERROR'
 plan=json.loads((P/'PROTOCOL.json').read_text());assert plan['markets']==json.loads((parent/'PROTOCOL.json').read_text())['markets'][1:]
 for i in plan['markets']:
  b=ROOT/'data/research/lan_worker_returns'/plan['baseline'][str(i)]['remote_relative'];r=json.loads((b/'result.json').read_text());assert all(abs(v)<1e-8 for v in r['clock_smoke']['final_pending_cash_direct'].values())
 out=dict(status='PASS',real_first_path_audit_regression=True,all9_existing_schema_checked=True,policy_and_overlay_byte_identity=True,native_executed=0,first_path_rerun=False,mandatory_first_fixture=x['mandatory'])
 (P/'AUDIT_REGRESSION.json').write_text(json.dumps(out,indent=2),encoding='utf-8');return out
if __name__=='__main__':print(json.dumps(run()))
