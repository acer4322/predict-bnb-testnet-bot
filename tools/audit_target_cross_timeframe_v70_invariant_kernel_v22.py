from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'

def load(name):
    return json.loads((P/name).read_text(encoding='utf-8'))

v5=load('TARGET_CROSS_TIMEFRAME_TRANSITION_MATRIX_SMOKE_V5_20260903.json')['summary']
v6=load('TARGET_CROSS_TIMEFRAME_MANAGEMENT_EXECUTION_SEPARATION_V6_20260903.json')['summary']
v11=load('TARGET_CROSS_TIMEFRAME_FIRST_OPPOSITE_ECONOMIC_CORE_V11_20260903.json')
v12=load('TARGET_CROSS_TIMEFRAME_MARGINAL_ECONOMIC_ROLE_V12_20260903.json')
v13=load('TARGET_CROSS_TIMEFRAME_PARTIAL_RESPONSIBILITY_PAYMENT_V13_20260903.json')
v14=load('TARGET_CROSS_TIMEFRAME_ACTIVE_PASSIVE_PARALLELISM_V14_20260903.json')
v15=load('TARGET_CROSS_TIMEFRAME_SAME_SIDE_PARALLEL_EXECUTION_V15_20260903.json')
v17=load('TARGET_BTC_CROSS_TIMEFRAME_SHARED_THESIS_REPLICATION_V17_20260903.json')

continuous=['BTC5M','BTC15M','BTC1H','ETH5M']
rows={}
for m in continuous:
    repair=v13[m]['REPAIR']; expand=v13[m]['EXPAND']
    rpar=v15[m]['REPAIR']; epar=v15[m]['EXPAND']
    rows[m]={
        'repairFloorImproveShare':v12[m]['REPAIR']['floorImproveShare'],
        'expandBestImproveShare':v12[m]['EXPAND']['bestImproveShare'],
        'repairMedianGapFraction':repair['medianFraction'],
        'repairCrossGapShare':repair['crossGapShare'],
        'repairNearFullGapShare':repair['nearFullGapShare'],
        'expandMedianGapFraction':expand['medianFraction'],
        'managementRepairToExpand':v6[m]['managementTransition']['REPAIR']['EXPAND'],
        'managementExpandToRepair':v6[m]['managementTransition']['EXPAND']['REPAIR'],
        'activeRepairSameSidePassiveWithin5Prior':rpar['within5'],
        'activeRepairSameSidePassiveWithin5Next':rpar['within5Next'],
        'activeExpandSameSidePassiveWithin5Prior':epar['within5'],
        'activeExpandSameSidePassiveWithin5Next':epar['within5Next'],
        'medianRepairExpandGapSec':v5[m]['medianRepairExpandGapSec'],
        'twoSidedFloorImproveShare':v11[m]['floorImproveShare'],
        'roleShares':v5[m]['roleShares'],
        'executionRouting':v6[m]['executionRouting'],
    }

# These are architecture-consistency checks only, deliberately broad and not promotion gates.
checks={
    'repairEconomicallyMeansFloorRecovery':all(rows[m]['repairFloorImproveShare']>=0.75 for m in continuous),
    'expandEconomicallyMeansUpsidePurchase':all(rows[m]['expandBestImproveShare']>=0.999 for m in continuous),
    'repairUsuallyNotExactBalanceCompletion':all(rows[m]['repairNearFullGapShare']<0.05 for m in continuous),
    'crossGapRepairExistsEverywhere':all(rows[m]['repairCrossGapShare']>0 for m in continuous),
    'repairExpandRelayExistsEverywhere':all(rows[m]['managementRepairToExpand']>0 and rows[m]['managementExpandToRepair']>0 for m in continuous),
    'sameSidePassiveActiveRepairParallelismExists':all(rows[m]['activeRepairSameSidePassiveWithin5Prior']>0.35 and rows[m]['activeRepairSameSidePassiveWithin5Next']>0.35 for m in continuous),
    'sameSidePassiveActiveExpandParallelismExists':all(rows[m]['activeExpandSameSidePassiveWithin5Prior']>0.30 and rows[m]['activeExpandSameSidePassiveWithin5Next']>0.30 for m in continuous),
    'wallClockStretchesAcrossBTCFrames':rows['BTC5M']['medianRepairExpandGapSec'] < rows['BTC15M']['medianRepairExpandGapSec'] < rows['BTC1H']['medianRepairExpandGapSec'],
    'btc5m15mSharedThesisReplicates':float(v17['BTC15M_vs_BTC5M']['alignShare'])>=0.80,
}

eth15={
    'repairFloorImproveShare':v12['ETH15M']['REPAIR']['floorImproveShare'],
    'expandBestImproveShare':v12['ETH15M']['EXPAND']['bestImproveShare'],
    'repairMedianGapFraction':v13['ETH15M']['REPAIR']['medianFraction'],
    'repairCrossGapShare':v13['ETH15M']['REPAIR']['crossGapShare'],
    'managementRepairToExpand':v6['ETH15M']['managementTransition']['REPAIR']['EXPAND'],
    'managementExpandToRepair':v6['ETH15M']['managementTransition']['EXPAND']['REPAIR'],
    'passiveRepairShare':v5['ETH15M']['roleShares']['PASSIVE_REPAIR'],
    'passiveExpandShare':v5['ETH15M']['roleShares']['PASSIVE_EXPAND'],
}

out={
    'version':'TARGET_CROSS_TIMEFRAME_V70_INVARIANT_KERNEL_AUDIT_V22',
    'date':'2026-09-03',
    'researchOnly':True,
    'actionAuthority':False,
    'purpose':'Test whether V70 responsibility/composite/generation architecture is a cross-timeframe invariant rather than ETH5M-specific.',
    'continuousFamily':continuous,
    'rows':rows,
    'checks':checks,
    'architectureConsistencyPass':all(checks.values()),
    'eth15mRegimeException':eth15,
    'derivedInterpretation':{
        'sharedKernel':[ 
            'REPAIR is floor-recovery responsibility',
            'EXPAND is upside-purchase responsibility',
            'Repair is usually tranche-based rather than exact gap completion',
            'cross-gap Repair exists in every continuous-family market and can create overflow',
            'Repair<->Expand relay exists in every continuous-family market',
            'Passive and Active execution coexist near the same role/side, supporting shared execution budgets',
            'management clocks should prefer event/progress/normalized phase over fixed seconds'
        ],
        'adapterSpecific':[ 
            'responsibility payment fraction / overflow size',
            'Passive vs Active routing proportions',
            'absolute wall-clock delay',
            'directional thesis horizon',
            'ETH15M sparse Active-only regime'
        ],
        'v70Implication':'Promote composite carrier as an architecture candidate, but keep quantity and execution routing market/regime-specific. Next experiment should test one physical responsibility budget with passive+active quotas and fill-conserved Repair->Expand allocation on 1-3 markets only.'
    },
    'boundary':[ 
        'Uses already-consumed V5/V6/V11-V15/V17 synthesis evidence; not a fresh promotion cohort.',
        'No controller tuning, no PnL optimization, no action authority.',
        'Does not prove the same numeric sizing across markets.',
        'Does not treat ETH15M as part of the continuous passive+active family.'
    ]
}

op=P/'TARGET_CROSS_TIMEFRAME_V70_INVARIANT_KERNEL_AUDIT_V22_20260903.json'
op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'ok':True,'output':str(op.relative_to(ROOT)),'architectureConsistencyPass':out['architectureConsistencyPass'],'checks':checks,'rows':rows,'eth15m':eth15},ensure_ascii=False))
