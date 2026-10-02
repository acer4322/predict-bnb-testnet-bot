import copy
import json
from pathlib import Path

import pytest

from tools.check_hft244_rebuilt_smoke_v1 import compare


def original_rows():
    root=Path(__file__).resolve().parents[1]
    return json.loads((root/'data/research/r4_v0/p0_provenance_v1/HFT244_PARTIAL_RECEIPT_ACCOUNTING_COMPACT_V1_20260910.json').read_text())['rows']


def test_baseline_comparison_accepts_saved_identity():
    rows=original_rows()
    compare(rows,copy.deepcopy(rows),'baseline')


def test_baseline_rejects_cash_change():
    ref=original_rows(); rows=copy.deepcopy(ref)
    rows[0]['snapshots'][1]['native']['balance']+=.01
    with pytest.raises(AssertionError,match='baseline accounting changed'):
        compare(rows,ref,'baseline')


def test_candidate_cannot_pass_old_partial_accounting():
    rows=original_rows()
    with pytest.raises(AssertionError,match='candidate accounting failed'):
        compare(rows,copy.deepcopy(rows),'candidate')


def test_candidate_rejects_changed_execution_even_if_accounting_flags_pass():
    ref=original_rows(); rows=copy.deepcopy(ref)
    for row in rows:
        for snap in row['snapshots']:
            snap['accountPass']=True
    rows[0]['snapshots'][1]['nowNs']+=1
    with pytest.raises(AssertionError,match='execution trace changed'):
        compare(rows,ref,'candidate')
