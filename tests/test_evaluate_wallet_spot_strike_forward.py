from __future__ import annotations

import json
import sqlite3

from tools.evaluate_wallet_spot_strike_forward import (
    evaluate,
    fixed_stake_counterfactual,
    stressed_performance,
    wilson_interval,
)


def _databases(shadow_path, simulation_path) -> None:
    shadow = sqlite3.connect(shadow_path)
    shadow.executescript(
        """
        CREATE TABLE wallet_spot_strike_forward_meta(cohort TEXT PRIMARY KEY,deployed_at_ms INTEGER,policy_json TEXT);
        CREATE TABLE wallet_spot_strike_forward_decisions(cohort TEXT,market_id INTEGER,market_bucket INTEGER,decision_at_ms INTEGER,seconds_left REAL,decision TEXT,reason TEXT,side TEXT,observed_ask REAL,start_price REAL,spot_price REAL,displacement_bps REAL,spot_age_ms REAL,prediction_receipt_age_ms REAL,source_market_id INTEGER,source_observation_id INTEGER,payload_json TEXT);
        CREATE TABLE wallet_spot_strike_forward_events(cohort TEXT,market_id INTEGER,market_bucket INTEGER,decision_at_ms INTEGER,seconds_left REAL,side TEXT,observed_ask REAL,effective_unit_cost REAL,stake_usdt REAL,shares REAL,start_price REAL,spot_price REAL,displacement_bps REAL,spot_age_ms REAL,prediction_receipt_age_ms REAL,source_market_id INTEGER,source_observation_id INTEGER,payload_json TEXT);
        CREATE TABLE wallet_spot_strike_forward_results(cohort TEXT,market_id INTEGER,market_bucket INTEGER,winner TEXT,resolved_at_ms INTEGER,side TEXT,observed_ask REAL,stake_usdt REAL,shares REAL,payout_usdt REAL,net_pnl_usdt REAL,net_roi REAL,status TEXT);
        CREATE TABLE wallet_shadow_target_events(wallet TEXT,market_id INTEGER,event_ms INTEGER,side TEXT,shares REAL,price REAL,role TEXT,quote_type TEXT);
        INSERT INTO wallet_spot_strike_forward_meta VALUES('SPOT_STRIKE_10S_V2',1000,'{"maxAsk":0.95}');
        INSERT INTO wallet_spot_strike_forward_decisions VALUES('SPOT_STRIKE_10S_V2',11,100,100090,9.5,'TRADE','LOCKED_POLICY_MATCH','UP',0.8,100,101,100,1,2,99,1,'{}');
        INSERT INTO wallet_spot_strike_forward_decisions VALUES('SPOT_STRIKE_10S_V2',12,100,100091,9.4,'SKIP','STALE_PREDICT_BOOK',NULL,NULL,NULL,NULL,NULL,NULL,4000,99,1,'{}');
        INSERT INTO wallet_spot_strike_forward_events VALUES('SPOT_STRIKE_10S_V2',11,100,100090,9.5,'UP',0.8,0.804,1,1.243781,100,101,100,1,2,99,1,'{}');
        INSERT INTO wallet_spot_strike_forward_results VALUES('SPOT_STRIKE_10S_V2',11,100,'UP',101000,'UP',0.8,1,1.243781,1.243781,0.243781,0.243781,'WIN');
        INSERT INTO wallet_shadow_target_events VALUES('0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03',11,100080,'UP',8,0.7,'TAKER','BID');
        INSERT INTO wallet_shadow_target_events VALUES('0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03',12,100080,'DOWN',8,0.7,'TAKER','BID');
        """
    )
    shadow.commit(); shadow.close()
    simulation = sqlite3.connect(simulation_path)
    simulation.executescript(
        """
        CREATE TABLE strategy_m_market_sequence(sequence_no INTEGER,market_id INTEGER,start_ms INTEGER);
        CREATE TABLE market_settlements(market_id INTEGER,official_winner TEXT,status TEXT,start_price REAL,official_end_price REAL);
        INSERT INTO strategy_m_market_sequence VALUES(1,99,100000);
        INSERT INTO market_settlements VALUES(99,'UP','OFFICIAL',100,101);
        """
    )
    simulation.commit(); simulation.close()


def test_wilson_interval_is_bounded() -> None:
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(8, 10)
    assert 0 < low < 0.8 < high < 1


def test_execution_stress_reprices_fixed_stake() -> None:
    result = stressed_performance([
        {"stake_usdt": 1.0, "observed_ask": 0.80, "status": "WIN"}
    ], 1)
    assert 0 < result["netRoi"] < 0.24


def test_high_ask_counterfactual_shows_thin_roi_even_when_all_win() -> None:
    result = fixed_stake_counterfactual([
        {"side": "UP", "officialWinner": "UP", "ask": 0.96},
        {"side": "DOWN", "officialWinner": "DOWN", "ask": 0.99},
    ])
    assert result["settled"] == 2
    assert result["winRate"] == 1
    assert 0 < result["netRoi"] < 0.03


def test_forward_report_joins_causal_target_and_official_result(tmp_path) -> None:
    shadow = tmp_path / "shadow.db"
    simulation = tmp_path / "simulation.db"
    _databases(shadow, simulation)
    report = evaluate(shadow, simulation)
    assert report["coverage"]["trades"] == 1
    assert report["directionSignal"]["winRate"] == 1
    assert report["targetSimilarity"]["atDecisionMatchRate"] == 1
    assert report["targetSimilarity"]["atDecisionComparable"] == 1
    assert report["targetSimilarity"]["capitalAtDecisionMatchRate"] == 1
    assert report["markets"][1]["directionCorrect"] is None
    assert report["markets"][0]["targetAtDecision"]["upCostUsdt"] == 5.6
    assert report["performance"]["netRoi"] > 0.24
    assert report["integrity"]["tradeDecisionEventCountMatch"] is True
    assert report["completion"]["eligible"] is False
