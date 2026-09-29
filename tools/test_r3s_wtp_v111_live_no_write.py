from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path: sys.path.insert(0,str(ROOT/'src'))
from predict_bot.unified_controller_r3s_r31_echtgeld_v1 import UnifiedControllerR3SR31EchtgeldV1, EngineDeterministicReject, VERSION, DISPLAY_VERSION
OUT=ROOT/'data/research/r3_v0/r3s_wtp_v112_live_no_write_regression.json'
def main():
 c=UnifiedControllerR3SR31EchtgeldV1(); calls=[]
 c._deployment_live_ready=lambda: True
 c._r3s_dynamic_taker_shares=lambda *a,**k: 10.0
 c.r3s_active_stack.readd_gate=lambda *a,**k:(True,None,None)
 c.r3s_active_stack.pre_add_evidence=lambda *a,**k:None
 def fake(path,payload):
  calls.append(dict(payload))
  raise EngineDeterministicReject({'order':{'error_kind':'TAKER_PRICE_CAP','state':'REJECTED'},'error':'synthetic price cap'},400)
 c._engine_post=fake
 snap={'marketId':999,'bucketStartSec':1,'windowEndMs':9999999999999,'secondsLeft':200,'sampledAtMs':1000}
 c.current_market_id=999
 c.inventory.reset()
 c.r3s_wtp_policy.reset_market()
 a=c._record_taker('DOWN',0.37,1000,'TEST:ADD1',snap,{},0,0,0,'ADD_EFFECT')
 b=c._record_taker('DOWN',0.45,5000,'TEST:ADD2',snap,{},0,0,0,'ADD_EFFECT')
 first=float(calls[0]['maxPrice']); second=float(calls[1]['maxPrice'])
 checks={'version':VERSION=='R3S_R31_V1_1_2_WTP1_MAKER_BRIDGE_FIX_ECHTGELD','display':DISPLAY_VERSION=='R3-S + R3.1 V1.1.2 WTP1 Maker Bridge Fix','firstCap':abs(first-.39)<1e-9,'noRatchet':abs(second-.39)<1e-9,'noPending':len(c.taker_pending)==0,'noFaultWait':not c.r21_active_fault_wait}
 out={'version':'R3S_WTP_V112_LIVE_NO_WRITE_REGRESSION','calls':calls,'checks':checks,'allPass':all(checks.values())};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8'); c.stop();print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)),'allPass':out['allPass'],'firstMaxPrice':first,'retryMaxPrice':second}))
if __name__=='__main__':main()
