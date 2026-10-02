from pathlib import Path
import sys,json,math
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
D=ROOT/'data/research/r3_v0'; ART=D/'r3_post_add_our_state_student_v2_stream.joblib'
MIDS=[1674459,1649545,1663753,1678736]
out=[]
for mid in MIDS:
 r=run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=ART,post_add_lifecycle_control=True)
 ev=[]
 for x in r.get('makerFillEvents',[]): ev.append((int(x['atMs']),'MAKER',str(x['side']),float(x['deltaShares']),float(x['price'])))
 for x in r.get('takerEvents',[]): ev.append((int(x['atMs']),'TAKER',str(x['side']),float(x['shares']),float(x['price'])))
 ev.sort()
 def geom(t):
  up=dn=cost=fees=0.0
  for et,role,side,sh,px in ev:
   if et>t: break
   if side=='UP': up+=sh
   else: dn+=sh
   cost+=sh*px; fees+=sh*px*.02 if role=='TAKER' else 0.0
  g=up+dn; net=up-dn; fl=min(up-cost-fees,dn-cost-fees); ups=max(up-cost-fees,dn-cost-fees)
  return {'floor':fl,'upside':ups,'absNet':abs(net),'pairedCoverage':2*min(up,dn)/g if g else 0.0,'gross':g}
 for a in r.get('takerAttempts',[]):
  if a.get('structuralEffect')!='ADD_EFFECT' or a.get('result')!='FILLED': continue
  t=int(a['fillMs']); g0=geom(t); g15=geom(t+15000); g30=geom(t+30000)
  out.append({'marketId':mid,'atMs':int(a['atMs']),'side':a['side'],'shares':float(a.get('filledShares') or 0.0),'g0':g0,'g15':g15,'g30':g30,'floorRecovery15':g15['floor']-g0['floor'],'floorRecovery30':g30['floor']-g0['floor'],'absNetChange15':g15['absNet']-g0['absNet'],'absNetChange30':g30['absNet']-g0['absNet'],'pairedChange15':g15['pairedCoverage']-g0['pairedCoverage'],'pairedChange30':g30['pairedCoverage']-g0['pairedCoverage']})
(D/'r3_add_formation_continuation_smoke4_v1.json').write_text(json.dumps({'version':'R3_ADD_FORMATION_CONTINUATION_SMOKE4_V1','rows':out},indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3_add_formation_continuation_smoke4_v1.json','rows':out}))
