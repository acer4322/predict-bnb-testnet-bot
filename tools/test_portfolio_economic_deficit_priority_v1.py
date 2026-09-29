from pathlib import Path
import sys
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.eth_repair_modular.portfolio_economic_deficit_priority import (
    PortfolioEconomicDeficitPriorityV1,
    PortfolioEconomicDeficitPriorityContext,
)

P=PortfolioEconomicDeficitPriorityV1()
rows=[]

def ck(name,ctx,allow,reason):
    d=P.evaluate(ctx)
    ok=bool(d.allow_candidate)==allow and d.reason==reason
    rows.append({'name':name,'pass':ok,'decision':d.__dict__})

ck('repair_never_blocked',PortfolioEconomicDeficitPriorityContext(True,-1.0,-1.2,'REPAIR'),True,'NON_EXPAND_NEVER_BLOCKED')
ck('no_deficit_expand_free',PortfolioEconomicDeficitPriorityContext(False,0.2,-0.3,'EXPAND'),True,'NO_OPEN_ECONOMIC_DEFICIT')
ck('floor_nonnegative_expand_free',PortfolioEconomicDeficitPriorityContext(True,0.0,-0.2,'EXPAND'),True,'NO_OPEN_ECONOMIC_DEFICIT')
ck('open_deficit_worsening_expand_deferred',PortfolioEconomicDeficitPriorityContext(True,-0.5,-0.8,'EXPAND'),False,'OPEN_ECONOMIC_DEFICIT_REPAIR_PRIORITY')
ck('open_deficit_owned_repair_exact_offset',PortfolioEconomicDeficitPriorityContext(True,-0.5,-0.5,'EXPAND'),True,'OPEN_DEFICIT_BUT_OWNED_REPAIR_COVERS_INCREMENTAL_DAMAGE')
ck('open_deficit_owned_repair_overoffset',PortfolioEconomicDeficitPriorityContext(True,-0.5,-0.3,'EXPAND'),True,'OPEN_DEFICIT_BUT_OWNED_REPAIR_COVERS_INCREMENTAL_DAMAGE')
ck('missing_projection_deferred',PortfolioEconomicDeficitPriorityContext(True,-0.5,None,'EXPAND'),False,'NO_JOINT_PORTFOLIO_FLOOR_PROJECTION')
ck('other_role_never_blocked',PortfolioEconomicDeficitPriorityContext(True,-1.0,-1.4,'HOLD'),True,'NON_EXPAND_NEVER_BLOCKED')

print({'passed':sum(r['pass'] for r in rows),'total':len(rows),'allPass':all(r['pass'] for r in rows),'rows':rows})
if not all(r['pass'] for r in rows): raise SystemExit(1)
