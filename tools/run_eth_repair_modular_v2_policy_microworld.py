from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
 from tools.eth_repair_modular.profile import economic_recursive_execution_v2_profile
 from tools.eth_repair_modular.contracts import RepairExecutionContext
except ImportError:
 z=Path(__file__).with_name('eth_repair_modular_v2.zip')
 if z.exists() and str(z) not in sys.path:sys.path.insert(0,str(z))
 from eth_repair_modular.profile import economic_recursive_execution_v2_profile
 from eth_repair_modular.contracts import RepairExecutionContext

def ctx(**kw):
 base=dict(t=1,seconds_left=240.0,parent_id=2,parent_side='DOWN',overflow_born_parent=True,armed=True,churn_count=1,payment_progress_since_arm=False,active_already_owned=False,hard_confirmed=False,floor=-0.5,manager_debt=0.4,live_ask=0.75,legal_physical_qty=1.3333333333333333)
 base.update(kw);return RepairExecutionContext(**base)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args();pol=economic_recursive_execution_v2_profile().repair_execution
 cases=[
  ('ordinary_inherits',ctx(overflow_born_parent=False),False,'ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION'),
  ('not_armed',ctx(armed=False),False,'NOT_ARMED'),
  ('active_owned',ctx(active_already_owned=True),False,'ACTIVE_ALREADY_OWNED'),
  ('hard_confirmed',ctx(hard_confirmed=True),False,'ACTIVE_ALREADY_OWNED'),
  ('payment_progress',ctx(payment_progress_since_arm=True),False,'PAYMENT_PROGRESS_CONTINUE_PASSIVE'),
  ('late',ctx(seconds_left=180.0),False,'LATE_NO_NEW_ACTIVE_EXPOSURE'),
  ('floor_safe',ctx(floor=0.0),False,'NO_NEGATIVE_FLOOR_REPAIR_NEED'),
  ('no_debt',ctx(manager_debt=0.0),False,'NO_NEGATIVE_FLOOR_REPAIR_NEED'),
  ('no_churn',ctx(churn_count=0),False,'WAIT_FOR_DISCONNECT_EVIDENCE'),
  ('no_frontier',ctx(live_ask=None,legal_physical_qty=None),False,'NO_EXECUTABLE_ACTIVE_FRONTIER'),
  ('illegal_large_slice',ctx(legal_physical_qty=12.1),False,'ILLEGAL_PHYSICAL_SLICE'),
  ('valid_single_disconnect',ctx(churn_count=1,manager_debt=0.46,legal_physical_qty=1.35),True,'OVERFLOW_PARENT_SINGLE_DISCONNECT_ACTIVE_COMPOSITE'),
  ('valid_repeated_new_parent_same_policy',ctx(parent_id=3,parent_side='UP',churn_count=1,manager_debt=0.89,live_ask=0.33,legal_physical_qty=3.03030303030303),True,'OVERFLOW_PARENT_SINGLE_DISCONNECT_ACTIVE_COMPOSITE'),
 ]
 rows=[];passed=0
 for name,c,allow,reason in cases:
  d=pol.evaluate(c);ok=(bool(d.allow_active_handoff)==allow and d.reason==reason and ((not allow and d.physical_qty==0.0) or (allow and abs(d.physical_qty-float(c.legal_physical_qty))<1e-9)))
  passed+=int(ok);rows.append({'case':name,'ok':ok,'decision':{'allow':d.allow_active_handoff,'physicalQty':d.physical_qty,'reason':d.reason}})
 out={'version':'ETH_REPAIR_MODULAR_V2_POLICY_MICROWORLD','policy':pol.name,'passed':passed,'total':len(rows),'functionalPass':passed==len(rows),'rows':rows}
 Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'passed':passed,'total':len(rows),'functionalPass':out['functionalPass']}))
if __name__=='__main__':main()
