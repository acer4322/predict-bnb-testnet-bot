import json
v9=json.load(open('data/research/lan_worker_returns/eth-repair-v9-conjunctive-full5/result.json'))
v10=json.load(open('data/research/lan_worker_returns/eth-repair-v10-continuous-repair-full5/result.json'))
a=[r for r in v9['rows'] if r['scenario']=='CONTROL']
b=[r for r in v10['rows'] if r['scenario']=='CONTROL']
A={r['marketId']:r for r in a}; B={r['marketId']:r for r in b}; mids=sorted(set(A)&set(B))
def agg(D):
    rs=[D[m] for m in mids]
    return {
        'markets':len(rs),
        'pnl':sum(r['pnlDiagnosticOnly'] for r in rs),
        'buy':sum(r['buyNotional'] for r in rs),
        'meanPairCoverage':sum(r['pairCoverage'] for r in rs)/len(rs),
        'meanAbsNet':sum(r['absNet'] for r in rs)/len(rs),
        'zeroCoverageMarkets':sum(r['pairCoverage']<=1e-9 for r in rs),
        'absNet5Markets':sum(r['absNet']>=4.999 for r in rs),
        'repairParentBirths':sum(r.get('repairParentBirths',0) for r in rs),
        'repairParentCompletions':sum(r.get('repairParentCompletions',0) for r in rs),
        'fills':sum(r['actualFillEvents'] for r in rs),
        'submits':sum(r['submits'] for r in rs),
    }
changed=[]
for m in mids:
    if abs(A[m]['pnlDiagnosticOnly']-B[m]['pnlDiagnosticOnly'])>1e-9 or abs(A[m]['absNet']-B[m]['absNet'])>1e-9 or abs(A[m]['pairCoverage']-B[m]['pairCoverage'])>1e-9:
        changed.append({
            'marketId':m,
            'v9Pnl':A[m]['pnlDiagnosticOnly'],'v10Pnl':B[m]['pnlDiagnosticOnly'],
            'v9Coverage':A[m]['pairCoverage'],'v10Coverage':B[m]['pairCoverage'],
            'v9AbsNet':A[m]['absNet'],'v10AbsNet':B[m]['absNet'],
            'v9Births':A[m].get('repairParentBirths',0),'v10Births':B[m].get('repairParentBirths',0),
        })
print(json.dumps({'V9':agg(A),'V10':agg(B),'changed':changed},indent=2))
