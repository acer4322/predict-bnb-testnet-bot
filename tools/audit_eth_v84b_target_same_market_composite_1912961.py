from __future__ import annotations
import json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V84B_SAME_MARKET_COMPOSITE_1912961_20260903.json'
MID=1912961
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
rows=con.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where market_id=? and asset='ETH' order by first_event_ms,parent_id",(MID,)).fetchall(); con.close()
u=d=cost=0.0; out=[]; start=int(rows[0]['first_event_ms']) if rows else 0
for r in rows:
    q=float(r['shares'] or 0); p=float(r['average_price'] or 0); side=str(r['side']); gap=abs(u-d)
    weak='UP' if u<d-1e-9 else 'DOWN' if d<u-1e-9 else None
    repair=min(q,gap) if side==weak else 0.0
    expand=q-repair
    f0=min(u,d)-cost; b0=max(u,d)-cost
    if side=='UP': u+=q
    else: d+=q
    cost+=q*p
    f1=min(u,d)-cost; b1=max(u,d)-cost
    out.append({'t':int(r['first_event_ms']),'secFromFirst':(int(r['first_event_ms'])-start)/1000.0,'role':str(r['role']),'side':side,'price':p,'qty':q,'weakSidePre':weak,'gapPre':gap,'repairAllocation':repair,'expandAllocation':expand,'composite':repair>1e-9 and expand>1e-9,'floorBefore':f0,'floorAfter':f1,'floorDelta':f1-f0,'bestBefore':b0,'bestAfter':b1,'bestDelta':b1-b0,'parentId':str(r['parent_id'])})
comps=[x for x in out if x['composite']]
pre180=[x for x in out if x['secFromFirst']<=120.0]  # first physical fill is ~market start+?; descriptive only
res={'version':'TARGET_ETH_V84B_SAME_MARKET_COMPOSITE_1912961','date':'2026-09-03','researchOnly':True,'marketId':MID,'method':'Target official parent fills; strict-past inventory role reconstruction; weak-side fill allocated Repair=min(q,pre-gap), overflow allocated Expand','summary':{'parentFills':len(out),'compositeParents':len(comps),'compositeShare':len(comps)/len(out) if out else 0.0,'makerComposite':sum(x['role']=='MAKER' for x in comps),'takerComposite':sum(x['role']=='TAKER' for x in comps),'compositeFloorImprove':sum(x['floorDelta']>1e-9 for x in comps),'compositeFloorImproveShare':sum(x['floorDelta']>1e-9 for x in comps)/len(comps) if comps else 0.0,'terminalFloor':min(u,d)-cost,'terminalBest':max(u,d)-cost},'compositeParents':comps,'first20':out[:20],'terminal':{'upShares':u,'downShares':d,'cost':cost,'floor':min(u,d)-cost,'best':max(u,d)-cost},'interpretationBoundary':['Offline Target post-market teacher analysis only','No Target future action used as OUR runtime trigger','Role reconstruction uses Target actual inventory, not OUR state']}
OUT.write_text(json.dumps(res,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'summary':res['summary'],'firstComposite':comps[:5]},ensure_ascii=False))
