import pandas as pd, numpy as np, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_joint_conversion_objective_v0_rows.csv'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_conversion_budget_v0_report.json'
df=pd.read_csv(P)
# Focus on states that were loser-aligned at first Taker. Quantify how much floor is spent per unit alignment recovery.
d=df[df.preClass=='LOSER'].copy()
report={'version':'TARGET_CONVERSION_BUDGET_V0','researchOnly':True,'rows':len(d),'horizons':{}}
for h in [5,15,30,60,120]:
    a=pd.to_numeric(d[f'dAlign_{h}s'],errors='coerce')
    f=pd.to_numeric(d[f'dFloor_{h}s'],errors='coerce')
    ok=a.notna()&f.notna()
    x=pd.DataFrame({'align':a[ok],'floor':f[ok]})
    x['spent']=(-x.floor).clip(lower=0)
    x['eff']=x['align']/x['spent'].replace(0,np.nan)
    useful=x[x['align']>0]
    report['horizons'][str(h)]={
      'n':len(x),'alignmentPositiveRate':float((x['align']>0).mean()),
      'meanAlign':float(x['align'].mean()),'meanFloorDelta':float(x['floor'].mean()),
      'medianSpentWhenUseful':float(useful.spent.median()) if len(useful) else None,
      'medianAlignWhenUseful':float(useful['align'].median()) if len(useful) else None,
      'medianEfficiencySharesPerDollar':float(useful.eff.replace([np.inf,-np.inf],np.nan).median()) if len(useful) else None,
      'usefulWithinBudget':{}
    }
    for b in [2,5,10,20,30,40,60]:
      z=x[(x['align']>0)&(x['spent']<=b)]
      report['horizons'][str(h)]['usefulWithinBudget'][str(b)]={
        'rateOfAll':float(len(z)/len(x)) if len(x) else 0,
        'n':len(z),'meanAlign':float(z['align'].mean()) if len(z) else None,
        'meanFloorDelta':float(z['floor'].mean()) if len(z) else None}
OUT.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))