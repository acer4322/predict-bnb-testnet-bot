from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import build_supervisor_curriculum_catalog_v0 as base

ROOT = Path(__file__).resolve().parents[1]
SRC_699 = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1' / 'target_general_maker_side_hazard_v1.csv'
SRC_80 = ROOT / 'data' / 'research' / 'supervisor_options_v0' / 'target_general_state_increment_v2.csv'
OUT = ROOT / 'data' / 'research' / 'supervisor_curriculum_v0'
COMBINED = OUT / 'target_general_state_combined_v1.csv'
CATALOG = OUT / 'ordinary_market_curriculum_catalog_v1.csv'
REPORT = OUT / 'ordinary_market_curriculum_catalog_v1_report.json'
EVENTS = OUT / 'ordinary_market_curriculum_events_v1.csv'


def main() -> int:
    a = pd.read_csv(SRC_699)
    b = pd.read_csv(SRC_80)
    d = (
        pd.concat([a, b], ignore_index=True)
        .drop_duplicates(['market_id', 'checkpoint_ms'], keep='last')
        .sort_values(['market_end_ms', 'market_id', 'checkpoint_ms'])
        .reset_index(drop=True)
    )
    assert d['market_id'].nunique() == 779, d['market_id'].nunique()
    COMBINED.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(COMBINED, index=False)

    base.GENERAL = COMBINED
    base.CATALOG = CATALOG
    base.REPORT = REPORT
    base.EVENTS = EVENTS
    rc = base.main()

    rep = json.loads(REPORT.read_text(encoding='utf-8'))
    rep['reportVersion'] = 'SUPERVISOR_CURRICULUM_CATALOG_V1'
    rep['source']['markets'] = int(d['market_id'].nunique())
    rep['source']['checkpointRows'] = int(len(d))
    rep['source']['minEndMs'] = int(d['market_end_ms'].min())
    rep['source']['maxEndMs'] = int(d['market_end_ms'].max())
    rep['source']['special20260816'] = 'ABSENT'
    rep['sourceFiles'] = {
        'base699': str(SRC_699.relative_to(ROOT)).replace('\\', '/'),
        'increment80': str(SRC_80.relative_to(ROOT)).replace('\\', '/'),
        'combined779': str(COMBINED.relative_to(ROOT)).replace('\\', '/'),
    }
    rep['guards'] = [
        'No winner/PnL.',
        'Ordinary-only 699 + 80 increment; sealed 2026-08-16 absent.',
        'Retrospective Target actions are curriculum/teacher labels only.',
        'No runtime changes.',
    ]
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
