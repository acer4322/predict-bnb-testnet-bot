from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_responsibility_supervisor_1513668_v1.json'
OUT=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_responsibility_supervisor_pnl_1513668_v1.json'
d=json.loads(SRC.read_text(encoding='utf-8'))
rows=[]
for c in d['cases']:
    cost=sum(float(x['price'])*float(x['shares']) for x in c['fills'])
    up=sum(float(x['shares']) for x in c['fills'] if x['side']=='UP')
    down=sum(float(x['shares']) for x in c['fills'] if x['side']=='DOWN')
    pnl=up-cost
    rows.append({'name':c['name'],'cost':cost,'upShares':up,'downShares':down,'diagnosticPnl':pnl,'frozen':c['frozen'],'escalations':c['escalations'],'note':'Escalated responsibility is not given a hypothetical fill; PnL therefore excludes unresolved escalation execution.'})
out={'version':'CAP100_RESPONSIBILITY_SUPERVISOR_PNL_1513668_V1','marketId':1513668,'winner':'UP','rows':rows}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
