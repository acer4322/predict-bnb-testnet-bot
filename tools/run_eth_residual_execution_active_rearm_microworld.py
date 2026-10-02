from __future__ import annotations
import json
from tools.eth_repair_modular.residual_execution_active_rearm import ResidualExecutionActiveRearmContext,ResidualExecutionActiveRearmPolicyV1
P=ResidualExecutionActiveRearmPolicyV1();rows=[]
def add(name,expect,**kw):
 d=P.evaluate(ResidualExecutionActiveRearmContext(**kw));rows.append({'name':name,'pass':d.allow_rearm==expect,'allow':d.allow_rearm,'reason':d.reason})
base=dict(parent_id=1,active_key='UP_5',active_live=False,active_cancel_pending=False,active_terminal_confirmed=True,active_actual_filled=1.7857142857142856,active_fill_reconciled_qty=1.7857142857142856,authoritative_residual_debt=1.1734693877551021,same_parent_other_live_carrier=False,pending_sibling_reconciliation=False)
add('1946784_terminal_reconciled_residual_rearm',True,**base)
for name,patch in [
 ('live_active_blocks',{'active_live':True}),('cancel_pending_blocks',{'active_cancel_pending':True}),('not_terminal_blocks',{'active_terminal_confirmed':False}),('unreconciled_fill_blocks',{'active_fill_reconciled_qty':1.0}),('no_residual_blocks',{'authoritative_residual_debt':0.0}),('other_live_sibling_blocks',{'same_parent_other_live_carrier':True}),('pending_sibling_reconcile_blocks',{'pending_sibling_reconciliation':True}),('no_active_blocks',{'active_key':None})]:
 x=dict(base);x.update(patch);add(name,False,**x)
# Zero-fill terminal ownership can rearm only when a residual responsibility still exists and accounting is current.
x=dict(base);x.update(active_actual_filled=0.0,active_fill_reconciled_qty=0.0);add('terminal_zero_fill_residual_rearm',True,**x)
out={'version':'RESIDUAL_EXECUTION_ACTIVE_REARM_MICROWORLD_V1','policy':P.name,'passed':sum(x['pass'] for x in rows),'total':len(rows),'allPass':all(x['pass'] for x in rows),'rows':rows};print(json.dumps(out,indent=2))
if not out['allPass']:raise SystemExit(1)
