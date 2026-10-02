from pathlib import Path
import json
import pandas as pd

OUT = Path('data/research/target_maker_taker_coordination_big_v1')
REPORT = OUT / 'target_blind_maker_lifecycle_bootstrap_occupancy_v1_report.json'
PREFIX = 'target_blind_maker_lifecycle_bootstrap_v0'


def load(suffs, kind):
    return pd.concat([pd.read_csv(OUT / f'{PREFIX}{s}_{kind}.csv') for s in suffs], ignore_index=True)


def summarize(name, suffs):
    st = load(suffs, 'states')
    ms = load(suffs, 'markets')
    for c in ['p_maker_up_1s','p_maker_down_1s','active_up_order','active_down_order']:
        st[c] = pd.to_numeric(st[c], errors='coerce').fillna(0.0)
    p_up = st.p_maker_up_1s
    p_dn = st.p_maker_down_1s
    a_up = (st.active_up_order > 0).astype(float)
    a_dn = (st.active_down_order > 0).astype(float)
    total_mass = float((p_up + p_dn).sum())
    occupied_mass = float((p_up * a_up + p_dn * a_dn).sum())
    free_mass = float((p_up * (1-a_up) + p_dn * (1-a_dn)).sum())
    intents = int(pd.to_numeric(ms.makerIntentUp, errors='coerce').fillna(0).sum() + pd.to_numeric(ms.makerIntentDown, errors='coerce').fillna(0).sum())
    placements = int(pd.to_numeric(ms.generatedPlacements, errors='coerce').fillna(0).sum())
    fills = int(pd.to_numeric(ms.generatedMakerFills, errors='coerce').fillna(0).sum())
    n = len(st)
    return {
        'name': name,
        'states': n,
        'markets': int(ms.ourMarketId.nunique()),
        'actual': {
            'intents': intents,
            'placements': placements,
            'fills': fills,
            'placementRealizationRate': placements / intents if intents else None,
            'intentsPer300StateSeconds': intents / n * 300 if n else None,
            'placementsPerMarket': placements / max(1, int(ms.ourMarketId.nunique())),
            'fillsPerMarket': fills / max(1, int(ms.ourMarketId.nunique())),
        },
        'hazardProbabilityMass': {
            'total': total_mass,
            'sameSideOccupied': occupied_mass,
            'sameSideFree': free_mass,
            'occupiedShare': occupied_mass / total_mass if total_mass else None,
            'freeShare': free_mass / total_mass if total_mass else None,
            'expectedTotalPer300StateSeconds': total_mass / n * 300 if n else None,
            'expectedOccupiedPer300StateSeconds': occupied_mass / n * 300 if n else None,
            'expectedFreePer300StateSeconds': free_mass / n * 300 if n else None,
        },
        'orderOccupancy': {
            'upActiveStateShare': float(a_up.mean()),
            'downActiveStateShare': float(a_dn.mean()),
            'bothActiveStateShare': float(((a_up > 0) & (a_dn > 0)).mean()),
            'neitherActiveStateShare': float(((a_up == 0) & (a_dn == 0)).mean()),
        },
        'side': {
            'UP': {
                'probabilityMass': float(p_up.sum()),
                'occupiedMass': float((p_up*a_up).sum()),
                'freeMass': float((p_up*(1-a_up)).sum()),
                'activeStateShare': float(a_up.mean()),
            },
            'DOWN': {
                'probabilityMass': float(p_dn.sum()),
                'occupiedMass': float((p_dn*a_dn).sum()),
                'freeMass': float((p_dn*(1-a_dn)).sum()),
                'activeStateShare': float(a_dn.mean()),
            }
        }
    }

v0 = summarize('LIFECYCLE_FEEDBACK_V0', [f'_c{i}' for i in range(6)])
v1 = summarize('COREBOOK_NO_LIFECYCLE_V1', [f'_corebook_c{i}' for i in range(6)])
rep = {
    'reportVersion': 'TARGET_BLIND_MAKER_BOOTSTRAP_OCCUPANCY_V1',
    'researchOnly': True,
    'question': 'When CORE+BOOK removes self-lifecycle hazard feedback, is the remaining placement bottleneck mainly hazard mass arriving while the same side already has a live resting order?',
    'variants': {'V0': v0, 'COREBOOK_V1': v1},
    'deltaCoreBookVsV0': {
        'actualIntents': v1['actual']['intents'] - v0['actual']['intents'],
        'actualPlacements': v1['actual']['placements'] - v0['actual']['placements'],
        'occupiedHazardMass': v1['hazardProbabilityMass']['sameSideOccupied'] - v0['hazardProbabilityMass']['sameSideOccupied'],
        'freeHazardMass': v1['hazardProbabilityMass']['sameSideFree'] - v0['hazardProbabilityMass']['sameSideFree'],
        'occupiedMassShare': v1['hazardProbabilityMass']['occupiedShare'] - v0['hazardProbabilityMass']['occupiedShare'],
        'bothActiveStateShare': v1['orderOccupancy']['bothActiveStateShare'] - v0['orderOccupancy']['bothActiveStateShare'],
    },
    'guard': 'Probability-mass decomposition is deterministic post-hoc on target-blind generated states. It diagnoses simulator/action-space occupancy; it does not imply Target has the same resting ownership state.'
}
REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(rep, ensure_ascii=False, indent=2))
