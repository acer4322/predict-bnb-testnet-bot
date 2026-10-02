from pathlib import Path
import json, torch
ROOT=Path(r'C:\BTC5M-worker\.lan_worker_v1\results')
base=torch.load(ROOT/'r4-adaptive-cycle-dual-loop-v1'/'champion.pt',map_location='cpu',weights_only=False)
hard=torch.load(ROOT/'r4-adaptive-cycle-cap-hard-v2'/'champion.pt',map_location='cpu',weights_only=False)
out={
  'version':'R4_ADAPTIVE_CYCLE_HYBRID_V3',
  'researchOnly':True,
  'actionAuthority':False,
  'repairState':hard['repairState'],
  'addState':base['addState']
}
od=ROOT/'r4-adaptive-cycle-hybrid-v3'; od.mkdir(parents=True,exist_ok=True)
torch.save(out,od/'champion.pt')
rep={'version':out['version'],'repairSource':'r4-adaptive-cycle-cap-hard-v2/champion.pt','addSource':'r4-adaptive-cycle-dual-loop-v1/champion.pt','training':'NONE','researchOnly':True,'actionAuthority':False}
(od/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
print(json.dumps(rep,indent=2))