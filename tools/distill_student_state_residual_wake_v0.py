from __future__ import annotations

import bisect
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TEMP_SCRIPT = ROOT / 'tools' / 'analyze_target_temporary_imbalance_recovery_v0.py'
DATA = OUT / 'student_state_residual_wake_v0.csv'
ART = OUT / 'student_state_residual_wake_p3_platt_v0.joblib'
REPORT = OUT / 'student_state_residual_wake_v0_report.json'
EPS = 1e-6

CHUNKS = [
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_dev00_04_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_dev00_04_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_dev05_09_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_dev05_09_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_dev10_14_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_dev10_14_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_dev15_19_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_dev15_19_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_dev20_24_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_dev20_24_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_distill25_34_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_distill25_34_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_distill35_44_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_distill35_44_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_distill45_54_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_distill45_54_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_distill55_64_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_distill55_64_markets.csv'),
    ('target_blind_promoted_controller_closed_loop_v7_residual_off_distill65_74_states.csv', 'target_blind_promoted_controller_closed_loop_v7_residual_off_distill65_74_markets.csv'),
]


def ro(path: Path):
    c = sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro', uri=True, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('pragma query_only=on')
    return c


def metric(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    both = len(set(y.tolist())) > 1
    return {
        'n': int(len(y)),
        'positives': int(y.sum()),
        'rate': float(y.mean()) if len(y) else None,
        'predMean': float(p.mean()) if len(p) else None,
        'auc': float(roc_auc_score(y, p)) if both else None,
        'ap': float(average_precision_score(y, p)) if y.sum() else None,
        'logLoss': float(log_loss(y, p, labels=[0, 1])) if len(y) else None,
        'brier': float(brier_score_loss(y, p)) if len(y) else None,
    }


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p)).reshape(-1, 1)


def load_temp_module():
    spec = importlib.util.spec_from_file_location('tmp_residual_teacher', TEMP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fresh_teacher(target_ids: set[int]):
    """Reconstruct fresh Big-V1-compatible REPAIR_EFFECT labels and overlap windows.

    Future Target actions are teacher labels only. No winner/PnL is read here.
    """
    tmp = load_temp_module()
    tc = ro(TARGET_DB)
    bc = ro(BOOK_DB)
    try:
        events = tmp.load_events(tc, target_ids)
        maker_parents = tmp.load_parents(bc, target_ids)
        takers: dict[int, list[dict]] = defaultdict(list)
        ids = sorted(target_ids)
        for st in range(0, len(ids), 300):
            batch = ids[st:st + 300]
            marks = ','.join('?' * len(batch))
            sql = (
                'select parent_id,market_id,side,first_event_ms,last_event_ms,average_price,shares,fill_legs '
                'from target_parent_orders where asset=\'BTC\' and role=\'TAKER\' and quote_type=\'BID\' '
                f'and market_id in ({marks}) order by market_id,first_event_ms,parent_id'
            )
            for row in tc.execute(sql, batch):
                takers[int(row['market_id'])].append(dict(row))

        overlap: dict[int, list[tuple[int, int]]] = {}
        repair: dict[int, list[int]] = {}
        effect_counts: dict[str, int] = defaultdict(int)

        for mid in ids:
            ev = events.get(mid, [])
            ps = maker_parents.get(mid, [])
            intervals: list[tuple[int, int]] = []

            # Same realized-overlap semantic as target_temporary_imbalance_recovery_v0.
            for p in ps:
                cp = int(p['placement_first_ms']) - 1
                side = str(p['target_side'])
                prior = [
                    q for q in ps
                    if q['parent_id'] != p['parent_id']
                    and str(q['target_side']) == side
                    and int(q['placement_first_ms']) <= cp < int(q['last_target_ms'])
                ]
                if not prior:
                    continue
                pre = tmp.inv_until(ev, cp, inclusive=True)
                if pre['makerDominant'] != side or float(pre['makerAbsNet']) < 18 - tmp.EPS:
                    continue
                anchor = int(p['last_target_ms'])
                post = tmp.inv_until(ev, anchor, inclusive=True)
                if float(post['makerAbsNet']) - float(pre['makerAbsNet']) > 1.0:
                    intervals.append((anchor + 1, anchor + 15001))
            overlap[mid] = sorted(intervals)

            repair_times: list[int] = []
            for tp in takers.get(mid, []):
                t = int(tp['first_event_ms'])
                pre = tmp.inv_until(ev, t - 1, inclusive=True)
                cn = float(pre['combinedNet'])
                side = str(tp['side'])
                shares = float(tp['shares'])
                post = cn + (shares if side == 'UP' else -shares)
                if abs(cn) < 1e-9:
                    effect = 'BUILD_FROM_FLAT'
                elif abs(post) < abs(cn) - 1e-9:
                    effect = 'REPAIR_EFFECT'
                elif abs(post) > abs(cn) + 1e-9:
                    effect = 'ADD_EFFECT'
                else:
                    effect = 'NEUTRAL_EFFECT'
                effect_counts[effect] += 1
                if effect == 'REPAIR_EFFECT' and not any(a <= t <= b for a, b in overlap[mid]):
                    repair_times.append(t)
            repair[mid] = sorted(repair_times)

        return repair, overlap, dict(effect_counts)
    finally:
        tc.close()
        bc.close()


def build():
    states = []
    maps = []
    for sf, mf in CHUNKS:
        states.append(pd.read_csv(OUT / sf))
        maps.append(pd.read_csv(OUT / mf, usecols=['ourMarketId', 'targetMarketId', 'windowEndMs']))

    d = pd.concat(states, ignore_index=True)
    mp = pd.concat(maps, ignore_index=True).drop_duplicates('ourMarketId')
    d = d.merge(mp, left_on='marketId', right_on='ourMarketId', how='inner', suffixes=('', '_map'))

    # Runtime eligibility: OUR has residual Maker inventory and no active OUR overlap episode.
    d = d[
        (pd.to_numeric(d.episodeActive, errors='coerce').fillna(0) == 0)
        & (pd.to_numeric(d.maker_abs_net, errors='coerce') > 1.0)
    ].copy()

    repair, overlap, effect_counts = fresh_teacher(set(mp.targetMarketId.astype(int)))
    labels = []
    excluded = []
    delays = []
    for row in d[['targetMarketId', 'atMs']].itertuples(index=False):
        mid = int(row.targetMarketId)
        t = int(row.atMs)
        inside = any(a <= t <= b for a, b in overlap.get(mid, []))
        excluded.append(inside)
        arr = repair.get(mid, [])
        i = bisect.bisect_right(arr, t)
        nxt = arr[i] if i < len(arr) else None
        delay = nxt - t if nxt is not None else None
        delays.append(delay if delay is not None else np.nan)
        labels.append(int((not inside) and delay is not None and 0 < delay <= 3000))

    d['teacher_target_overlap_window'] = excluded
    d['label_target_residual_repair_next3s'] = labels
    d['teacher_next_repair_delay_ms'] = delays
    d = d[~d.teacher_target_overlap_window].copy()
    d.attrs['teacher_effect_counts'] = effect_counts
    d.attrs['teacher_repair_events'] = sum(len(v) for v in repair.values())
    d.attrs['teacher_overlap_intervals'] = sum(len(v) for v in overlap.values())
    return d.sort_values(['windowEndMs', 'atMs', 'marketId']).reset_index(drop=True)


def main():
    d = build()
    teacher_effect_counts = dict(d.attrs.get('teacher_effect_counts', {}))
    teacher_repair_events = int(d.attrs.get('teacher_repair_events', 0))
    teacher_overlap_intervals = int(d.attrs.get('teacher_overlap_intervals', 0))
    d.to_csv(DATA, index=False)

    markets = d[['marketId', 'windowEndMs']].drop_duplicates().sort_values(['windowEndMs', 'marketId'])
    ids = markets.marketId.astype(int).tolist()
    # Explicit DAgger-like chronology: first 50 train, next 12 validation, final 13 exposed markets test.
    split = {'train': set(ids[:50]), 'validation': set(ids[50:62]), 'test': set(ids[62:75])}
    parts = {k: d[d.marketId.astype(int).isin(v)].copy() for k, v in split.items()}

    raw_train = pd.to_numeric(parts['train'].pTaker3s, errors='coerce').fillna(0).to_numpy()
    y_train = parts['train'].label_target_residual_repair_next3s.astype(int).to_numpy()
    if len(set(y_train.tolist())) < 2:
        raise RuntimeError(f'fresh teacher join still has single-class train labels: positives={int(y_train.sum())} rows={len(y_train)}')

    cal = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000, random_state=20260820)
    cal.fit(logit(raw_train), y_train)

    results = {}
    for name, x in parts.items():
        y = x.label_target_residual_repair_next3s.astype(int).to_numpy()
        raw = pd.to_numeric(x.pTaker3s, errors='coerce').fillna(0).to_numpy()
        calibrated = cal.predict_proba(logit(raw))[:, 1]
        r = metric(y, raw)
        c = metric(y, calibrated)
        results[name] = {
            'rawFrozenP3OnOurState': r,
            'ourStatePlatt': c,
            'delta': {
                k: c[k] - r[k]
                for k in ['auc', 'ap', 'logLoss', 'brier', 'predMean']
                if c[k] is not None and r[k] is not None
            },
        }

    payload = {
        'version': 'STUDENT_STATE_RESIDUAL_WAKE_P3_PLATT_V0',
        'calibrator': cal,
        'input': 'logit(frozen pTaker3s scored on OUR R1 own-state)',
        'trainingMarkets': sorted(split['train']),
        'trainingMaxWindowEndMs': int(parts['train'].windowEndMs.max()),
        'semantics': 'DAgger-like calibration: OUR R1 own-state + same-market/time fresh Target residual REPAIR onset teacher label. Wake only; never direct Taker.',
    }
    joblib.dump(payload, ART)

    report = {
        'reportVersion': 'STUDENT_STATE_RESIDUAL_WAKE_V0',
        'researchOnly': True,
        'runtimeTargetDataAllowed': False,
        'question': 'Does frozen Target-state p3 preserve residual-repair ranking on current OUR own-state, and can train-only student-state calibration fix probability scale?',
        'dataset': {
            'rows': int(len(d)),
            'markets': int(d.marketId.nunique()),
            'positiveRate': float(d.label_target_residual_repair_next3s.mean()),
            'file': str(DATA),
            'eligibility': 'OUR episodeActive==0 and maker_abs_net>1; fresh Target realized-overlap +15s windows excluded',
            'teacherLabel': 'fresh target_parent_orders + wallet_shadow_target_events reconstruct Big V1 REPAIR_EFFECT at same market/time; starts within3s; label only',
            'teacherEffectCounts': teacher_effect_counts,
            'teacherResidualRepairEvents': teacher_repair_events,
            'teacherOverlapIntervals': teacher_overlap_intervals,
        },
        'splitMarkets': {k: len(v) for k, v in split.items()},
        'chronology': {
            'trainMaxWindowEndMs': int(parts['train'].windowEndMs.max()),
            'validationMaxWindowEndMs': int(parts['validation'].windowEndMs.max()),
            'testMaxWindowEndMs': int(parts['test'].windowEndMs.max()),
            'finalStart75Untouched': True,
        },
        'calibrator': {'coef': float(cal.coef_[0, 0]), 'intercept': float(cal.intercept_[0])},
        'results': results,
        'artifact': str(ART),
        'decisionRule': 'If raw p3 ranking remains strong on OUR-state, prefer calibration-only. If AUC materially collapses, do not force calibration; then test a full own-state corrective student. No PnL selection.',
        'guards': [
            'No OUR PnL/winner used in training or model selection.',
            'Target future action used only as teacher label.',
            'No final start75-99 market used.',
            '8784 R1 frozen.',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
