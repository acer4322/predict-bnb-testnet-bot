from __future__ import annotations
import json
from tools.eth_repair_modular.spread_aware_maker_priority import *
P=SpreadAwareMakerPriorityPolicyV1();rows=[]
def chk(name,ctx,pred):
 d=P.evaluate(ctx);rows.append({'name':name,'pass':bool(pred(d)),'price':d.price,'improved':d.improved,'reason':d.reason})
chk('one_tick_spread_stays_best',SpreadAwareMakerPriorityContext(.66,.67),lambda d:not d.improved and abs(d.price-.66)<1e-9)
chk('two_tick_spread_improves_one_tick',SpreadAwareMakerPriorityContext(.64,.66),lambda d:d.improved and abs(d.price-.65)<1e-9)
chk('three_tick_spread_still_only_one_tick',SpreadAwareMakerPriorityContext(.60,.63),lambda d:d.improved and abs(d.price-.61)<1e-9)
chk('ceiling_blocks_improvement',SpreadAwareMakerPriorityContext(.64,.66,.01,.645),lambda d:not d.improved and abs(d.price-.64)<1e-9)
chk('ceiling_allows_improvement',SpreadAwareMakerPriorityContext(.64,.66,.01,.65),lambda d:d.improved and abs(d.price-.65)<1e-9)
chk('never_crosses_ask',SpreadAwareMakerPriorityContext(.58,.60),lambda d:d.price<.60)
chk('invalid_spread_blocks',SpreadAwareMakerPriorityContext(.60,.60),lambda d:not d.improved)
chk('custom_tick_respected',SpreadAwareMakerPriorityContext(.50,.54,.02),lambda d:d.improved and abs(d.price-.52)<1e-9)
out={'version':'SPREAD_AWARE_MAKER_PRIORITY_MICROWORLD_V1','passed':sum(x['pass'] for x in rows),'total':len(rows),'allPass':all(x['pass'] for x in rows),'cases':rows,'boundary':['one venue tick maximum improvement','never crosses ask','optional economic ceiling','no Target price/tick heuristic; venue tick only']}
print(json.dumps(out,ensure_ascii=False))
