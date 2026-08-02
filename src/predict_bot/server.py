from __future__ import annotations

import json
import ipaddress
import hashlib
import math
import os
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .core import (
    BinanceClient,
    BinancePredictionClient,
    binance_top_of_book,
    effective_taker_cost,
    select_binary_market,
    taker_fee,
)
from .microstructure import MicrostructureObserver, PREDICTION_VERIFIED_ORIENTATIONS
from .m_realtime import MSeriesRealtimeEngine, direct_rest_prediction_event
from .live_trading import LiveM0WEngine, M01O_F1_MIN_SECONDS_LEFT
from .market_observer import MarketStateObserver, summarize_m01_settled_fills
from .supervisor import API_RESTART_EXIT_CODE
from .research_forward import (
    CONFIRMATION_ADD_FEE_BPS,
    CONFIRMATION_ADD_SOURCE_STRATEGIES,
    CONFIRMATION_ADD_STRATEGY,
    CONFIRMATION_ADD_TRANCHE_USDT,
    CONTINUOUS_CALIBRATION_RULES,
    CONTINUOUS_CALIBRATION_STRATEGIES,
    FUTURES_LEAD_EXPERIMENT_STRATEGIES,
    FUTURES_LEAD_EXIT_STRATEGIES,
    FUTURES_LEAD_FILTER_RULES,
    FUTURES_LEAD_FILTER_STRATEGIES,
    FUTURES_LEAD_OBSERVER_STRATEGIES,
    FUTURES_LEAD_OBSERVER_STRATEGY_VERSION,
    OBSERVER_AUTO_V6_SLOW_WINDOW,
    OBSERVER_AUTO_V6_STRATEGIES,
    OBSERVER_AUTO_V6_STRATEGY_RULES,
    OBSERVER_COMBINATION_STRATEGIES,
    OBSERVER_COMBINATION_STRATEGY_RULES,
    PRIMARY_RESEARCH_STRATEGIES,
    RESEARCH_PARAMETERS,
    RESEARCH_STRATEGIES,
    SHADOW_RESEARCH_STRATEGIES,
    ResearchSampleBuffer,
    confirmation_add_book_event_key,
    confirmation_add_book_is_safe,
    confirmation_add_execution_price,
    confirmation_add_levels,
    continuous_calibration_decision,
    execution_candidate as research_execution_candidate,
    filtered_futures_lead_signal,
    futures_lead_observer_decision,
    observer_v6_auto_decision,
    regime_futures_lead_signal as research_regime_futures_lead_signal,
    reverse_futures_lead_signal as research_reverse_futures_lead_signal,
    sampling_active as research_sampling_active,
    signal_for_strategy as research_signal_for_strategy,
)


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_SIM_DB", ROOT / "data" / "simulation.db"))
LIVE_DB_PATH = Path(
    os.environ.get("PREDICT_LIVE_DB", ROOT / "data" / "live_m0w.db")
)
API_HOST = os.environ.get("PREDICT_SIM_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("PREDICT_SIM_PORT", "8766"))
COLLECT_INTERVAL_SECONDS = max(0.25, float(os.environ.get("PREDICT_COLLECT_INTERVAL", "1.0")))
SETTLEMENT_RECHECK_SECONDS = max(
    5.0, float(os.environ.get("PREDICT_SETTLEMENT_RECHECK_INTERVAL", "15.0"))
)

DEFAULT_CONFIG: dict[str, float | bool] = {
    "strategy_a_enabled": False,
    "strategy_a_window_seconds": 120,
    "strategy_a_min_gap": 0.20,
    "strategy_a_max_entry": 0.20,
    "strategy_a_target": 0.40,
    "strategy_a_stake": 10.0,
    "strategy_b_enabled": True,
    "strategy_b_last_seconds": 20,
    "strategy_b_min_price": 0.90,
    "strategy_b_max_price": 0.95,
    "strategy_b_stake": 10.0,
    "strategy_b_stop_loss_price": 0.60,
    "strategy_b2_enabled": True,
    "strategy_b2_last_seconds": 20.0,
    "strategy_b2_min_seconds_left": 3.0,
    "strategy_b2_min_price": 0.90,
    "strategy_b2_max_price": 0.95,
    "strategy_b2_min_stake": 5.0,
    "strategy_b2_max_stake": 20.0,
    "strategy_b2_size_curve": 2.0,
    "strategy_b2_stop_loss_price": 0.60,
    "strategy_c_enabled": False,
    "strategy_c_window_seconds": 120,
    "strategy_c_min_gap": 0.20,
    "strategy_c_max_entry": 0.20,
    "strategy_c_target_multiplier": 2.0,
    "strategy_c_stake": 10.0,
    "strategy_d_enabled": False,
    "strategy_d_window_seconds": 120,
    "strategy_d_min_gap": 0.20,
    "strategy_d_max_first_entry": 0.30,
    "strategy_d_max_pair_cost": 0.95,
    "strategy_d_max_wait_seconds": 120,
    "strategy_d_force_exit_seconds": 20,
    "strategy_d_stake": 10.0,
    "strategy_e_enabled": True,
    "strategy_e_max_seconds_left": 90,
    "strategy_e_min_seconds_left": 30,
    "strategy_e_min_spot_move_bps": 3.0,
    "strategy_e_min_entry": 0.60,
    "strategy_e_max_entry": 0.90,
    "strategy_e_max_spread": 0.04,
    "strategy_e_min_baseline_seconds_left": 290,
    "strategy_e_stake": 10.0,
    "strategy_f_enabled": True,
    "strategy_f_window_seconds": 120,
    "strategy_f_min_gap": 0.20,
    "strategy_f_max_first_entry": 0.30,
    "strategy_f_max_pair_cost": 0.90,
    "strategy_f_max_wait_seconds": 120,
    "strategy_f_force_exit_seconds": 20,
    "strategy_f_stake": 5.0,
    "strategy_e2_enabled": True,
    "strategy_e2_max_seconds_left": 60,
    "strategy_e2_min_seconds_left": 30,
    "strategy_e2_min_entry": 0.60,
    "strategy_e2_max_entry": 0.79,
    "strategy_e2_max_spread": 0.02,
    "strategy_e2_min_baseline_seconds_left": 290,
    "strategy_e2_lookback_observations": 300,
    "strategy_e2_min_return_samples": 60,
    "strategy_e2_min_move_z": 0.75,
    "strategy_e2_sigma_floor": 0.00002,
    "strategy_e2_max_book_skew_ms": 500.0,
    "strategy_e2_max_book_age_ms": 2000.0,
    "strategy_e2_stake": 10.0,
    "strategy_g_enabled": True,
    "strategy_g_max_seconds_left": 180,
    "strategy_g_min_seconds_left": 30,
    "strategy_g_min_entry": 0.55,
    "strategy_g_max_entry": 0.85,
    "strategy_g_max_spread": 0.02,
    "strategy_g_min_baseline_seconds_left": 290,
    "strategy_g_lookback_observations": 120,
    "strategy_g_min_return_samples": 60,
    "strategy_g_sigma_floor": 0.00002,
    "strategy_g_probability_shrinkage": 0.50,
    "strategy_g_latency_seconds": 2.0,
    "strategy_g_uncertainty_margin": 0.03,
    "strategy_g_min_net_edge": 0.05,
    "strategy_g_spread_edge_multiplier": 3.0,
    "strategy_g_slippage_bps": 50.0,
    "strategy_g_max_book_skew_ms": 500.0,
    "strategy_g_max_book_age_ms": 2000.0,
    "strategy_g_stake": 10.0,
    "strategy_h_enabled": True,
    "strategy_h_leader_min_price": 0.90,
    "strategy_h_reversal_seconds": 5.0,
    "strategy_h_anchor_lookback_seconds": 2.5,
    "strategy_h_entry_window_seconds": 20.0,
    "strategy_h_max_entry": 0.01,
    "strategy_h_required_reversals": 2.0,
    "strategy_h_max_consecutive_losses": 2.0,
    "strategy_h_max_book_skew_ms": 500.0,
    "strategy_h_max_book_age_ms": 2000.0,
    "strategy_h_stake": 10.0,
    "strategy_i_enabled": True,
    "strategy_i_max_entry": 0.01,
    "strategy_i_min_seconds_left": 3.0,
    "strategy_i_stake": 1.0,
    "strategy_i_max_book_skew_ms": 500.0,
    "strategy_i_max_book_age_ms": 2000.0,
    "strategy_j_enabled": False,
    "strategy_j_window_seconds": 120.0,
    "strategy_j_min_seconds_left": 3.0,
    "strategy_j_min_gap": 0.20,
    "strategy_j_max_entry": 0.20,
    "strategy_j_target_multiplier": 2.0,
    "strategy_j_max_book_skew_ms": 500.0,
    "strategy_j_max_book_age_ms": 2000.0,
    "strategy_j_stake": 10.0,
    "strategy_k_enabled": True,
    "strategy_k_max_seconds_left": 180.0,
    "strategy_k_min_seconds_left": 20.0,
    "strategy_k_min_baseline_seconds_left": 290.0,
    "strategy_k_lookback_observations": 120.0,
    "strategy_k_min_return_samples": 60.0,
    "strategy_k_sigma_floor": 0.00002,
    "strategy_k_basis_uncertainty_bps": 1.5,
    "strategy_k_calibration_slope": 0.50,
    "strategy_k_latency_seconds": 2.0,
    "strategy_k_min_probability": 0.65,
    "strategy_k_max_spread": 0.02,
    "strategy_k_min_net_edge": 0.05,
    "strategy_k_spread_edge_multiplier": 3.0,
    "strategy_k_slippage_bps": 50.0,
    "strategy_k_max_book_skew_ms": 500.0,
    "strategy_k_max_book_age_ms": 2000.0,
    "strategy_k_stake": 10.0,
    "strategy_l_enabled": False,
    "strategy_l_window_seconds": 60.0,
    "strategy_l_max_entry": 0.50,
    "strategy_l_target": 0.70,
    "strategy_l_max_book_skew_ms": 500.0,
    "strategy_l_max_book_age_ms": 2000.0,
    "strategy_l_total_stake": 10.0,
    "strategy_m_enabled": False,
    "strategy_m_entry_window_seconds": 10.0,
    "strategy_m_stake": 10.0,
    "strategy_m_max_book_skew_ms": 500.0,
    "strategy_m_max_book_age_ms": 2000.0,
    "strategy_m0_enabled": True,
    "strategy_m0_entry_window_seconds": 10.0,
    "strategy_m0_stake": 10.0,
    "strategy_m0_max_book_skew_ms": 500.0,
    "strategy_m0_max_book_age_ms": 2000.0,
    "strategy_m0_seed": 20260717.0,
    "strategy_m01_enabled": True,
    "strategy_m01_entry_window_seconds": 300.0,
    "strategy_m01_max_entry": 0.30,
    "strategy_m01_stake": 10.0,
    "strategy_m01_max_book_skew_ms": 500.0,
    "strategy_m01_max_book_age_ms": 2000.0,
    "strategy_m01t180_enabled": True,
    "strategy_m01t180_min_seconds_left": 180.0,
    "strategy_m01t180_max_entry": 0.30,
    "strategy_m01t180_stake": 10.0,
    "strategy_m01t180_max_book_skew_ms": 500.0,
    "strategy_m01t180_max_book_age_ms": 2000.0,
    "strategy_m01t180d_enabled": True,
    "strategy_m01tasym_enabled": True,
    "strategy_pair_arb_010_enabled": True,
    "strategy_pair_arb_qc_015_enabled": True,
    "strategy_pair_arb_020_enabled": True,
    "strategy_pair_arb_risk_020_enabled": True,
    "strategy_pair_arb_stake": 10.0,
    "strategy_pair_arb_max_book_skew_ms": 500.0,
    "strategy_pair_arb_max_book_age_ms": 2000.0,
    "strategy_m01o_enabled": True,
    "strategy_m01o_entry_window_seconds": 300.0,
    "strategy_m01o_max_entry": 0.30,
    "strategy_m01o_stake": 10.0,
    "strategy_m01o_max_book_skew_ms": 500.0,
    "strategy_m01o_max_book_age_ms": 2000.0,
    "strategy_m01o_min_observer_samples": 6.0,
    "strategy_m01o_min_current_range_score": 2.0,
    "strategy_m01o_f1_enabled": True,
    "strategy_m01o_live_enabled": True,
    "strategy_m01f_enabled": True,
    "strategy_m01f_entry_window_seconds": 300.0,
    "strategy_m01f_min_entry": 0.20,
    "strategy_m01f_max_entry": 0.30,
    "strategy_m01f_stake": 10.0,
    "strategy_m01f_max_book_skew_ms": 500.0,
    "strategy_m01f_max_book_age_ms": 2000.0,
    "strategy_m01r_enabled": True,
    "strategy_m01r_entry_window_seconds": 300.0,
    "strategy_m01r_max_anchor": 0.30,
    "strategy_m01r_rebound": 0.10,
    "strategy_m01r_stake": 10.0,
    "strategy_m01r_max_book_skew_ms": 500.0,
    "strategy_m01r_max_book_age_ms": 2000.0,
    "strategy_m0w_enabled": True,
    "strategy_m0w_entry_window_seconds": 10.0,
    "strategy_m0w_stake": 10.0,
    "strategy_m0w_max_book_skew_ms": 500.0,
    "strategy_m0w_max_book_age_ms": 2000.0,
    "strategy_m01w_enabled": True,
    "strategy_m01w_entry_window_seconds": 300.0,
    "strategy_m01w_max_entry": 0.30,
    "strategy_m01w_stake": 10.0,
    "strategy_m01w_max_book_skew_ms": 500.0,
    "strategy_m01w_max_book_age_ms": 2000.0,
    "strategy_m1_enabled": True,
    "strategy_m1_entry_window_seconds": 10.0,
    "strategy_m1_stake": 10.0,
    "strategy_m1_max_book_skew_ms": 500.0,
    "strategy_m1_max_book_age_ms": 2000.0,
    "strategy_m2_enabled": False,
    "strategy_m2_entry_window_seconds": 10.0,
    "strategy_m2_stake": 10.0,
    "strategy_m2_max_book_skew_ms": 500.0,
    "strategy_m2_max_book_age_ms": 2000.0,
    "strategy_m3_enabled": True,
    "strategy_m3_entry_window_seconds": 10.0,
    "strategy_m3_stake": 10.0,
    "strategy_m3_max_book_skew_ms": 500.0,
    "strategy_m3_max_book_age_ms": 2000.0,
    "strategy_m3_min_abs_delta_bps": 1.0,
    "strategy_m4_enabled": False,
    "strategy_m4_entry_window_seconds": 10.0,
    "strategy_m4_stake": 10.0,
    "strategy_m4_max_book_skew_ms": 500.0,
    "strategy_m4_max_book_age_ms": 2000.0,
    "strategy_m4_required_observations": 2.0,
    "strategy_m5_enabled": False,
    "strategy_m5_entry_window_seconds": 10.0,
    "strategy_m5_stake": 10.0,
    "strategy_m5_max_book_skew_ms": 500.0,
    "strategy_m5_max_book_age_ms": 2000.0,
    "strategy_m6_enabled": False,
    "strategy_m6_entry_window_seconds": 10.0,
    "strategy_m6_stake": 10.0,
    "strategy_m6_max_book_skew_ms": 500.0,
    "strategy_m6_max_book_age_ms": 2000.0,
    "strategy_m6_min_basis_samples": 20.0,
    "strategy_m7_entry_window_seconds": 10.0,
    "strategy_m7_stake": 10.0,
    "strategy_m7_max_book_skew_ms": 500.0,
    "strategy_m7_max_book_age_ms": 2000.0,
    "strategy_m7_execution_grace_seconds": 2.0,
    "strategy_m7_1_enabled": True,
    "strategy_m7_2_enabled": True,
    "strategy_m7_3_enabled": True,
    "strategy_m7_5_enabled": True,
    # Five primary research strategies share one deliberately small virtual
    # capital pool. Counterfactual variants are isolated paper shadows.
    "strategy_r_microprice_enabled": True,
    "strategy_r_microprice_stake": 5.0,
    "strategy_r_ofi_enabled": True,
    "strategy_r_ofi_stake": 5.0,
    "strategy_r_ofi_min040_enabled": True,
    "strategy_r_ofi_min040_stake": 5.0,
    "strategy_r_ofi_event_cum_enabled": True,
    "strategy_r_ofi_event_cum_stake": 5.0,
    "strategy_r_ofi_event_cum_filtered_enabled": True,
    "strategy_r_ofi_event_cum_filtered_stake": 5.0,
    "strategy_r_futures_lead_enabled": True,
    "strategy_r_futures_lead_stake": 5.0,
    "strategy_r_futures_lead_continuous_v2_enabled": True,
    "strategy_r_futures_lead_continuous_v2_stake": 5.0,
    "strategy_r_futures_lead_reverse_enabled": True,
    "strategy_r_futures_lead_reverse_stake": 5.0,
    "strategy_r_futures_lead_regime_reverse_3l_enabled": True,
    "strategy_r_futures_lead_regime_reverse_3l_stake": 5.0,
    # 0=AUTO (three-loss regime), 1=force forward, 2=force reverse.
    "strategy_r_futures_lead_regime_reverse_3l_direction_mode": 0.0,
    "strategy_r_futures_lead_exit30_enabled": True,
    "strategy_r_futures_lead_exit30_stake": 5.0,
    "strategy_r_futures_lead_distance_enabled": True,
    "strategy_r_futures_lead_distance_stake": 5.0,
    "strategy_r_futures_lead_exit30_distance_enabled": True,
    "strategy_r_futures_lead_exit30_distance_stake": 5.0,
    "strategy_r_futures_lead_signal_100_enabled": True,
    "strategy_r_futures_lead_signal_100_stake": 5.0,
    "strategy_r_futures_lead_min_entry_020_enabled": True,
    "strategy_r_futures_lead_min_entry_020_stake": 5.0,
    "strategy_r_futures_lead_observer_f1_enabled": True,
    "strategy_r_futures_lead_observer_f1_stake": 5.0,
    "strategy_r_futures_lead_observer_v2_enabled": True,
    "strategy_r_futures_lead_observer_v2_stake": 5.0,
    "strategy_r_futures_lead_observer_v3_enabled": True,
    "strategy_r_futures_lead_observer_v3_stake": 5.0,
    "strategy_r_futures_lead_observer_v4_enabled": True,
    "strategy_r_futures_lead_observer_v4_stake": 5.0,
    "strategy_r_futures_lead_observer_v6_enabled": True,
    "strategy_r_futures_lead_observer_v6_stake": 5.0,
    "strategy_r_ofi_observer_v3_enabled": True,
    "strategy_r_ofi_observer_v3_stake": 5.0,
    "strategy_r_microprice_observer_v3_enabled": True,
    "strategy_r_microprice_observer_v3_stake": 5.0,
    "strategy_r_microprice_observer_v6_enabled": True,
    "strategy_r_microprice_observer_v6_stake": 5.0,
    "strategy_r_calibrated_value_observer_v6_enabled": True,
    "strategy_r_calibrated_value_observer_v6_stake": 5.0,
    "strategy_r_microprice_observer_auto_v6_enabled": True,
    "strategy_r_microprice_observer_auto_v6_stake": 5.0,
    "strategy_r_calibrated_value_observer_auto_v6_enabled": True,
    "strategy_r_calibrated_value_observer_auto_v6_stake": 5.0,
    "strategy_r_calibrated_value_enabled": True,
    "strategy_r_calibrated_value_stake": 5.0,
    "strategy_r_calibrated_value_continuous_v2_enabled": True,
    "strategy_r_calibrated_value_continuous_v2_stake": 5.0,
    "strategy_r_consensus_enabled": True,
    "strategy_r_consensus_stake": 5.0,
    "strategy_research_shared_cap_usdt": 100.0,
    "strategy_research_min_stake_usdt": 2.0,
    "strategy_research_slippage_bps": 50.0,
    "strategy_research_min_entry": 0.05,
    "strategy_research_max_spread": 0.03,
    "strategy_research_max_book_skew_ms": 500.0,
    "strategy_research_max_book_age_ms": 2000.0,
    "strategy_mx_enabled": False,
    "strategy_m0x_enabled": False,
    "strategy_mx_t60_enabled": False,
    "strategy_mx_t80_enabled": False,
    "strategy_mx_t98_enabled": False,
}

STRATEGY_K_FORECAST_CHECKPOINTS = (180, 120, 60, 30)
M_SERIES_STRATEGIES = (
    "M", "M0", "M01", "M01T180", "M01T180D", "M01TASYM", "M01O", "M01O_F1", "M01O_LIVE", "M01F", "M01R", "M0W", "M01W", "M1", "M2", "M3", "M4", "M5", "M6",
    "M7_1", "M7_2", "M7_3", "M7_5",
)
M7_DELAYS = {"M7_1": 1.0, "M7_2": 2.0, "M7_3": 3.0, "M7_5": 5.0}
M6_BASIS_CAPTURE_WINDOW_SECONDS = 10.0
M_MARKET_DURATION_MS = 300_000
M_MARKET_ADJACENCY_TOLERANCE_MS = 2_000
PAIR_ARB_THRESHOLDS = {
    "PAIR_ARB_010": 0.010,
    "PAIR_ARB_QC_015": 0.015,
    "PAIR_ARB_020": 0.020,
    "PAIR_ARB_RISK_020": -0.020,
}
MX_STRATEGIES = (
    "MX_T60", "MX_T70", "MX_T80", "MX_T90", "MX_T98",
    "MX_P50", "MX_P10", "MX_REV",
)
M0X_STRATEGIES = (
    "M0X_T60", "M0X_T70", "M0X_T80", "M0X_T90", "M0X_T98",
    "M0X_P50", "M0X_P10", "M0X_REV",
)
MX_ALL_STRATEGIES = (*MX_STRATEGIES, *M0X_STRATEGIES)
MX_FIXED_TARGETS = {
    "MX_T60": 0.60,
    "MX_T70": 0.70,
    "MX_T80": 0.80,
    "MX_T90": 0.90,
    "MX_T98": 0.98,
    "M0X_T60": 0.60,
    "M0X_T70": 0.70,
    "M0X_T80": 0.80,
    "M0X_T90": 0.90,
    "M0X_T98": 0.98,
}
MX_ENTRY_LIMIT = 0.50
MX_LEDGER_VERSION = "MX_v2_ws_limit_vwap_partial_fill_exit_branches"
SUPPORTED_STRATEGIES = (
    "A", "B", "B2", "C", "D", "E", "F", "E2", "G", "H", "I", "J", "K", "L",
    *M_SERIES_STRATEGIES,
    *RESEARCH_STRATEGIES,
    CONFIRMATION_ADD_STRATEGY,
)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def allowed_dashboard_origin(origin: str) -> bool:
    """Allow the dashboard only from loopback or a private LAN address."""
    try:
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.port != 4310 or not parsed.hostname:
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        address = ipaddress.ip_address(parsed.hostname)
        return address.is_loopback or address.is_private
    except (ValueError, TypeError):
        return False


def allowed_live_rules_request(client_host: str, origin: str, api_host: str) -> bool:
    """Allow rule writes from loopback or this dashboard over a private LAN."""
    try:
        requester = ipaddress.ip_address(str(client_host).split("%", 1)[0])
    except (ValueError, TypeError):
        return False
    if requester.is_loopback:
        return True
    if not requester.is_private or not allowed_dashboard_origin(origin):
        return False
    try:
        origin_host = urlsplit(origin).hostname
        api_hostname = urlsplit(f"http://{api_host}").hostname
        if not origin_host or not api_hostname:
            return False
        origin_address = ipaddress.ip_address(origin_host.split("%", 1)[0])
        api_address = ipaddress.ip_address(api_hostname.split("%", 1)[0])
        return origin_address.is_private and origin_address == api_address
    except (ValueError, TypeError):
        return False


def allowed_manual_sell_request(
    client_host: str,
    origin: str,
    api_host: str,
    confirmation_header: str,
) -> bool:
    """Allow a confirmed manual exit from loopback or the matching LAN dashboard."""
    if str(confirmation_header).strip().lower() != "confirmed":
        return False
    if not allowed_live_rules_request(client_host, origin, api_host):
        return False
    try:
        requester = ipaddress.ip_address(str(client_host).split("%", 1)[0])
        if requester.is_loopback:
            return True
        api_port = urlsplit(f"http://{api_host}").port
        configured_port = int(os.environ.get("PREDICT_SIM_PORT", "8766"))
        return requester.is_private and api_port == configured_port
    except (ValueError, TypeError):
        return False


def _timestamp_seconds(value: Any) -> float | None:
    """Normalize an ISO timestamp or numeric epoch for volatility calculations."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def rolling_volatility_per_sqrt_second(
    samples: list[tuple[Any, float]],
    *,
    min_return_samples: int,
    sigma_floor: float,
) -> tuple[float, int, bool] | None:
    """Estimate realized log-return sigma as sqrt(sum(r^2) / sum(dt))."""
    squared_returns = 0.0
    total_seconds = 0.0
    return_count = 0
    for (raw_t0, raw_p0), (raw_t1, raw_p1) in zip(samples, samples[1:]):
        t0 = _timestamp_seconds(raw_t0)
        t1 = _timestamp_seconds(raw_t1)
        p0 = float(raw_p0)
        p1 = float(raw_p1)
        if t0 is None or t1 is None or t1 <= t0 or p0 <= 0 or p1 <= 0:
            continue
        log_return = math.log(p1 / p0)
        squared_returns += log_return * log_return
        total_seconds += t1 - t0
        return_count += 1
    if return_count < int(min_return_samples) or total_seconds <= 0:
        return None
    realized_sigma = math.sqrt(squared_returns / total_seconds)
    floor_applied = realized_sigma < float(sigma_floor)
    return max(realized_sigma, float(sigma_floor)), return_count, floor_applied


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def temperature_calibrate_probability(probability: float, slope: float) -> float:
    """Shrink an extreme binary probability by scaling its log odds."""
    clipped = min(1.0 - 1e-12, max(1e-12, float(probability)))
    calibrated_logit = float(slope) * math.log(clipped / (1.0 - clipped))
    if calibrated_logit >= 0:
        return 1.0 / (1.0 + math.exp(-calibrated_logit))
    exponential = math.exp(calibrated_logit)
    return exponential / (1.0 + exponential)


def has_crossed_book(snapshot: dict[str, Any]) -> bool:
    for side in ("up", "down"):
        ask = snapshot.get(f"{side}_ask")
        bid = snapshot.get(f"{side}_bid")
        if ask is not None and bid is not None and float(ask) < float(bid):
            return True
    return False


def strategy_config_snapshot(
    config: dict[str, float | bool], strategy: str
) -> dict[str, float | bool]:
    prefix = f"strategy_{strategy.lower()}_"
    return {key: value for key, value in config.items() if key.startswith(prefix)}


def top_ask_fill(
    requested_stake: float, entry: float, visible_size: Any
) -> dict[str, float | bool] | None:
    """Cap a simulated top-of-book fill at the positive visible share quantity."""
    if visible_size is None:
        return None
    visible_shares = float(visible_size)
    if not math.isfinite(visible_shares) or visible_shares <= 0:
        return None
    requested_shares = requested_stake / entry
    filled_shares = min(requested_shares, visible_shares)
    filled_stake = filled_shares * entry
    return {
        "requested_stake": requested_stake,
        "requested_shares": requested_shares,
        "filled_stake": filled_stake,
        "filled_shares": filled_shares,
        "fill_ratio": filled_shares / requested_shares,
        "partial_fill": filled_shares < requested_shares,
    }


def _normalized_ask_levels(
    levels: Any,
    *,
    fallback_price: float,
    fallback_size: float,
) -> list[list[float]]:
    normalized: list[list[float]] = []
    for level in levels if isinstance(levels, list) else []:
        try:
            price = float(level.get("price") if isinstance(level, dict) else level[0])
            size = float(
                level.get("size", level.get("quantity"))
                if isinstance(level, dict)
                else level[1]
            )
        except (IndexError, TypeError, ValueError):
            continue
        if math.isfinite(price) and math.isfinite(size) and 0 < price <= 1 and size > 0:
            normalized.append([price, size])
    if not normalized:
        normalized.append([fallback_price, fallback_size])
    normalized.sort(key=lambda level: level[0])
    return normalized


def pair_orderbook_fill(
    up_levels: list[list[float]],
    down_levels: list[list[float]],
    *,
    stake: float,
    fee_bps: int,
    minimum_edge: float,
) -> dict[str, float | int | bool] | None:
    """Walk both asks and fill equal shares while the marginal edge survives."""
    requested_shares = stake / (up_levels[0][0] + down_levels[0][0])
    remaining_shares = requested_shares
    remaining_stake = stake
    up_index = down_index = 0
    up_remaining = up_levels[0][1]
    down_remaining = down_levels[0][1]
    filled_shares = up_cost = down_cost = up_fee = down_fee = 0.0
    up_levels_used: set[int] = set()
    down_levels_used: set[int] = set()

    while (
        remaining_shares > 1e-12
        and remaining_stake > 1e-12
        and up_index < len(up_levels)
        and down_index < len(down_levels)
    ):
        up_price = up_levels[up_index][0]
        down_price = down_levels[down_index][0]
        unit_up_fee = taker_fee(1.0, up_price, fee_bps)
        unit_down_fee = taker_fee(1.0, down_price, fee_bps)
        marginal_edge = (
            1.0 - up_price - down_price - unit_up_fee - unit_down_fee
        )
        if marginal_edge + 1e-12 < minimum_edge:
            break
        pair_notional = up_price + down_price
        quantity = min(
            remaining_shares,
            up_remaining,
            down_remaining,
            remaining_stake / pair_notional,
        )
        if quantity <= 1e-12:
            break
        filled_shares += quantity
        up_cost += quantity * up_price
        down_cost += quantity * down_price
        up_fee += taker_fee(quantity, up_price, fee_bps)
        down_fee += taker_fee(quantity, down_price, fee_bps)
        remaining_shares -= quantity
        remaining_stake -= quantity * pair_notional
        up_remaining -= quantity
        down_remaining -= quantity
        up_levels_used.add(up_index)
        down_levels_used.add(down_index)
        if up_remaining <= 1e-12:
            up_index += 1
            if up_index < len(up_levels):
                up_remaining = up_levels[up_index][1]
        if down_remaining <= 1e-12:
            down_index += 1
            if down_index < len(down_levels):
                down_remaining = down_levels[down_index][1]

    if filled_shares <= 1e-12:
        return None
    total_cost = up_cost + down_cost + up_fee + down_fee
    return {
        "requested_shares": requested_shares,
        "filled_shares": filled_shares,
        "fill_ratio": filled_shares / requested_shares,
        "partial_fill": filled_shares + 1e-12 < requested_shares,
        "up_vwap": up_cost / filled_shares,
        "down_vwap": down_cost / filled_shares,
        "up_fee": up_fee,
        "down_fee": down_fee,
        "total_cost": total_cost,
        "payout": filled_shares,
        "locked_pnl": filled_shares - total_cost,
        "net_edge_per_share": (filled_shares - total_cost) / filled_shares,
        "up_levels_consumed": len(up_levels_used),
        "down_levels_consumed": len(down_levels_used),
    }


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._read_only = False
        # Collector writes, realtime strategy decisions, Observer reads, and
        # dashboard statistics share this database.  WAL lets the independent
        # Observer connection read a stable snapshot without waiting behind
        # every one-second collector commit.  NORMAL is SQLite's documented
        # durable/recommended pairing for WAL and avoids unnecessary fsync
        # pressure on this high-frequency paper ledger.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        self._m_basis_cache: dict[
            tuple[int, int, int], dict[str, Any] | None
        ] = {}
        self._m_basis_sample_generation = 0
        self._m_existing_trade_cache: dict[int, set[str]] = {}
        self._research_samples = ResearchSampleBuffer()
        self._m_signal_row_cache: dict[tuple[str, int], sqlite3.Row | None] = {}
        self._m01o_gate_decision_cache: dict[
            tuple[str, int], tuple[str, str, bool]
        ] = {}
        self._m7_signal_row_cache: dict[int, sqlite3.Row | None] = {}
        self._mx_intent_markets: set[tuple[str, int]] = set()
        self._mx_entry_expired_markets: set[int] = set()
        self._mx_spot_quiet_markets: set[int] = set()
        self._config_cache: dict[str, float | bool] | None = None
        self._init()
        self._m_basis_sample_generation = int(
            self.db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM strategy_m_basis_samples"
            ).fetchone()[0]
        )

    @classmethod
    def open_read_only(cls, path: Path) -> "Store":
        """Open an isolated dashboard connection that cannot block Store's lock.

        SQLite WAL keeps these reads consistent while the collector and paper
        engines continue writing through the primary Store connection.
        """
        instance = cls.__new__(cls)
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        instance.db = sqlite3.connect(
            uri,
            uri=True,
            check_same_thread=False,
            timeout=5.0,
        )
        instance.db.row_factory = sqlite3.Row
        instance.db.execute("PRAGMA query_only=ON")
        instance.db.execute("PRAGMA busy_timeout=5000")
        instance.lock = threading.RLock()
        instance._read_only = True
        instance._config_cache = None
        return instance

    def drawdown_control_market_history(
        self, current_market_id: int, limit: int
    ) -> list[dict[str, Any]]:
        """Return only already-official markets preceding the live signal."""
        normalized_limit = max(1, int(limit))
        with self.lock:
            rows = self.db.execute(
                """SELECT market_id, start_price, official_end_price
                   FROM market_settlements
                   WHERE status='OFFICIAL' AND market_id < ?
                   ORDER BY market_id DESC
                   LIMIT ?""",
                (int(current_market_id), normalized_limit),
            ).fetchall()
        return [
            {
                "market_id": int(row["market_id"]),
                "start_price": row["start_price"],
                "end_price": row["official_end_price"],
            }
            for row in reversed(rows)
        ]

    def _init(self) -> None:
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    spot_price REAL NOT NULL,
                    spot_age_ms REAL,
                    futures_price REAL,
                    futures_timestamp_ms REAL,
                    futures_age_ms REAL,
                    futures_agg_trade_id INTEGER,
                    seconds_left REAL NOT NULL,
                    up_ask REAL,
                    up_bid REAL,
                    down_ask REAL,
                    down_bid REAL,
                    up_ask_size REAL,
                    up_bid_size REAL,
                    down_ask_size REAL,
                    down_bid_size REAL,
                    book_skew_ms REAL,
                    up_book_timestamp_ms REAL,
                    down_book_timestamp_ms REAL,
                    book_age_ms REAL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    target_price REAL,
                    exit_price REAL,
                    stake REAL NOT NULL,
                    shares REAL NOT NULL,
                    fees REAL NOT NULL DEFAULT 0,
                    fee_rate_bps INTEGER NOT NULL DEFAULT 200,
                    pnl REAL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    note TEXT,
                    strategy_version TEXT,
                    model_probability REAL,
                    model_edge REAL,
                    model_sigma REAL,
                    diagnostics_json TEXT
                );
                CREATE INDEX IF NOT EXISTS trades_market_strategy_idx
                    ON trades(market_id, strategy);
                CREATE INDEX IF NOT EXISTS trades_strategy_id_idx
                    ON trades(strategy, id);
                CREATE TABLE IF NOT EXISTS confirmation_add_shadow_state (
                    source_trade_id INTEGER PRIMARY KEY,
                    shadow_trade_id INTEGER NOT NULL UNIQUE,
                    source_strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    base_price REAL NOT NULL,
                    levels_json TEXT NOT NULL,
                    fills_json TEXT NOT NULL,
                    last_event_key TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    officially_settled_at TEXT,
                    FOREIGN KEY(source_trade_id) REFERENCES trades(id),
                    FOREIGN KEY(shadow_trade_id) REFERENCES trades(id)
                );
                CREATE INDEX IF NOT EXISTS confirmation_add_shadow_market_idx
                    ON confirmation_add_shadow_state(market_id, status);
                CREATE TABLE IF NOT EXISTS confirmation_add_shadow_meta (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    forward_started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_pair_arb_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    opened_at TEXT NOT NULL,
                    signal_timestamp TEXT NOT NULL,
                    seconds_left REAL NOT NULL,
                    up_ask REAL NOT NULL,
                    down_ask REAL NOT NULL,
                    up_ask_size REAL NOT NULL,
                    down_ask_size REAL NOT NULL,
                    requested_shares REAL NOT NULL DEFAULT 0,
                    shares REAL NOT NULL,
                    fill_ratio REAL NOT NULL DEFAULT 0,
                    partial_fill INTEGER NOT NULL DEFAULT 0,
                    up_fill_vwap REAL,
                    down_fill_vwap REAL,
                    up_levels_consumed INTEGER NOT NULL DEFAULT 0,
                    down_levels_consumed INTEGER NOT NULL DEFAULT 0,
                    up_fee REAL NOT NULL,
                    down_fee REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    payout REAL NOT NULL,
                    locked_pnl REAL NOT NULL,
                    net_edge_per_share REAL NOT NULL,
                    stressed_pnl_005 REAL NOT NULL,
                    stressed_pnl_010 REAL NOT NULL,
                    book_skew_ms REAL NOT NULL,
                    book_age_ms REAL NOT NULL,
                    fee_rate_bps INTEGER NOT NULL,
                    diagnostics_json TEXT,
                    UNIQUE(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS strategy_pair_arb_strategy_id_idx
                    ON strategy_pair_arb_trades(strategy, id);
                CREATE TABLE IF NOT EXISTS strategy_pair_arb_market_stats (
                    market_id INTEGER PRIMARY KEY,
                    topic_id INTEGER NOT NULL,
                    first_evaluated_at TEXT NOT NULL,
                    last_evaluated_at TEXT NOT NULL,
                    evaluations INTEGER NOT NULL DEFAULT 0,
                    valid_book_evaluations INTEGER NOT NULL DEFAULT 0,
                    eligible_010_snapshots INTEGER NOT NULL DEFAULT 0,
                    eligible_qc_015_snapshots INTEGER NOT NULL DEFAULT 0,
                    eligible_020_snapshots INTEGER NOT NULL DEFAULT 0,
                    rejected_invalid_book INTEGER NOT NULL DEFAULT 0,
                    rejected_book_skew INTEGER NOT NULL DEFAULT 0,
                    rejected_book_age INTEGER NOT NULL DEFAULT 0,
                    rejected_edge INTEGER NOT NULL DEFAULT 0,
                    best_net_edge REAL,
                    last_net_edge REAL,
                    last_up_ask REAL,
                    last_down_ask REAL,
                    last_book_skew_ms REAL,
                    last_book_age_ms REAL,
                    last_reason TEXT NOT NULL,
                    book_source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS strategy_pair_arb_stats_last_idx
                    ON strategy_pair_arb_market_stats(last_evaluated_at DESC);
                CREATE TABLE IF NOT EXISTS strategy_measurement_resets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    cutoff_trade_id INTEGER NOT NULL,
                    reset_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observations_market_idx
                    ON observations(market_id, id DESC);
                CREATE TABLE IF NOT EXISTS strategy_h_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL CHECK (mode IN ('DETECTING', 'ARMED')),
                    reversal_streak INTEGER NOT NULL DEFAULT 0,
                    loss_streak INTEGER NOT NULL DEFAULT 0,
                    last_processed_market_id INTEGER,
                    armed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_h_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    market_id INTEGER NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    winner TEXT NOT NULL,
                    official INTEGER NOT NULL,
                    candidate_side TEXT,
                    candidate_price REAL,
                    candidate_seconds_left REAL,
                    mode_before TEXT NOT NULL,
                    mode_after TEXT NOT NULL,
                    reversal_streak INTEGER NOT NULL,
                    loss_streak INTEGER NOT NULL,
                    details_json TEXT
                );
                CREATE INDEX IF NOT EXISTS strategy_h_events_market_idx
                    ON strategy_h_events(market_id, id DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS strategy_h_events_market_unique_idx
                    ON strategy_h_events(market_id);
                CREATE TABLE IF NOT EXISTS market_settlements (
                    market_id INTEGER PRIMARY KEY,
                    topic_id INTEGER,
                    start_price REAL,
                    proxy_winner TEXT,
                    official_winner TEXT,
                    official_end_price REAL,
                    status TEXT NOT NULL CHECK (status IN ('PENDING', 'OFFICIAL')),
                    first_settled_at TEXT NOT NULL,
                    official_settled_at TEXT,
                    check_attempts INTEGER NOT NULL DEFAULT 0,
                    last_checked_at TEXT,
                    h_processed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS market_settlements_pending_idx
                    ON market_settlements(status, first_settled_at);
                CREATE TABLE IF NOT EXISTS strategy_m_market_sequence (
                    sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL UNIQUE,
                    topic_id INTEGER NOT NULL,
                    start_ms INTEGER,
                    end_ms INTEGER,
                    previous_market_id INTEGER,
                    previous_is_adjacent INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS strategy_m_market_sequence_start_idx
                    ON strategy_m_market_sequence(start_ms DESC);
                CREATE TABLE IF NOT EXISTS strategy_k_forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    topic_id INTEGER NOT NULL,
                    market_id INTEGER NOT NULL,
                    checkpoint_seconds INTEGER NOT NULL,
                    seconds_left REAL NOT NULL,
                    signal_side TEXT NOT NULL CHECK (signal_side IN ('UP', 'DOWN')),
                    model_probability REAL NOT NULL,
                    raw_probability REAL NOT NULL,
                    model_sigma REAL NOT NULL,
                    adjusted_spot REAL NOT NULL,
                    official_strike REAL NOT NULL,
                    current_spot REAL NOT NULL,
                    anchor_spot REAL NOT NULL,
                    initial_basis_bps REAL NOT NULL,
                    official_winner TEXT,
                    forecast_correct INTEGER,
                    settled_official INTEGER NOT NULL DEFAULT 0,
                    diagnostics_json TEXT,
                    UNIQUE(market_id, checkpoint_seconds)
                );
                CREATE INDEX IF NOT EXISTS strategy_k_forecasts_market_idx
                    ON strategy_k_forecasts(market_id, checkpoint_seconds);
                CREATE TABLE IF NOT EXISTS strategy_m4_state (
                    market_id INTEGER PRIMARY KEY,
                    last_event_key TEXT NOT NULL,
                    last_side TEXT,
                    consecutive_observations INTEGER NOT NULL DEFAULT 0,
                    observations_json TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_m_signals (
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    signal_timestamp TEXT,
                    signal_elapsed REAL NOT NULL,
                    signal_received_monotonic_ns INTEGER,
                    signal_exchange_event_ms REAL,
                    signal_json TEXT NOT NULL,
                    realtime_context_json TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(strategy, market_id)
                );
                CREATE TABLE IF NOT EXISTS strategy_m01o_gate_decisions (
                    strategy TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    candidate_entry_price REAL NOT NULL,
                    candidate_stake REAL NOT NULL,
                    candidate_fees REAL NOT NULL DEFAULT 0,
                    first_evaluated_at TEXT NOT NULL,
                    last_evaluated_at TEXT NOT NULL,
                    evaluations INTEGER NOT NULL DEFAULT 1,
                    last_decision TEXT NOT NULL CHECK (last_decision IN ('ALLOW', 'BLOCK')),
                    last_block_category TEXT NOT NULL,
                    ever_allowed INTEGER NOT NULL DEFAULT 0,
                    opened INTEGER NOT NULL DEFAULT 0,
                    gate_json TEXT NOT NULL,
                    PRIMARY KEY(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS strategy_m01o_gate_profile_idx
                    ON strategy_m01o_gate_decisions(strategy, first_evaluated_at);
                CREATE TABLE IF NOT EXISTS strategy_m01r_state (
                    market_id INTEGER PRIMARY KEY,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    low_ask REAL NOT NULL,
                    trigger_price REAL NOT NULL,
                    armed_at TEXT NOT NULL,
                    low_observed_at TEXT NOT NULL,
                    low_event_sequence TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_m_basis_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id INTEGER NOT NULL UNIQUE,
                    captured_at TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    spot_price REAL NOT NULL,
                    basis_bps REAL NOT NULL,
                    signal_received_monotonic_ns INTEGER,
                    signal_event_sequence TEXT,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_m7_signals (
                    market_id INTEGER PRIMARY KEY,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    signal_timestamp TEXT,
                    signal_elapsed REAL NOT NULL,
                    signal_received_monotonic_ns INTEGER,
                    signal_json TEXT NOT NULL,
                    realtime_context_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_mx_positions (
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    status TEXT NOT NULL,
                    signal_timestamp TEXT,
                    signal_received_monotonic_ns INTEGER,
                    signal_event_sequence TEXT,
                    signal_spot_price REAL NOT NULL,
                    start_price REAL NOT NULL,
                    entry_deadline_elapsed REAL NOT NULL,
                    requested_entry_shares REAL NOT NULL,
                    filled_entry_shares REAL NOT NULL DEFAULT 0,
                    entry_cost REAL NOT NULL DEFAULT 0,
                    entry_fees REAL NOT NULL DEFAULT 0,
                    reference_entry_price REAL,
                    requested_exit_shares REAL NOT NULL DEFAULT 0,
                    filled_exit_shares REAL NOT NULL DEFAULT 0,
                    exit_proceeds REAL NOT NULL DEFAULT 0,
                    exit_fees REAL NOT NULL DEFAULT 0,
                    remaining_shares REAL NOT NULL DEFAULT 0,
                    remaining_cost_basis REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    reverse_triggered INTEGER NOT NULL DEFAULT 0,
                    rev_bid_armed INTEGER NOT NULL DEFAULT 0,
                    rev_spot_armed INTEGER NOT NULL DEFAULT 0,
                    close_reason TEXT,
                    diagnostics_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT,
                    PRIMARY KEY(strategy, market_id)
                );
                CREATE INDEX IF NOT EXISTS strategy_mx_positions_market_idx
                    ON strategy_mx_positions(market_id, strategy);
                CREATE INDEX IF NOT EXISTS strategy_mx_positions_strategy_idx
                    ON strategy_mx_positions(strategy, created_at DESC);
                CREATE TABLE IF NOT EXISTS strategy_mx_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('ENTRY', 'EXIT')),
                    stage_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    limit_price REAL,
                    requested_shares REAL NOT NULL,
                    filled_shares REAL NOT NULL DEFAULT 0,
                    expires_elapsed REAL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(strategy, market_id, action, stage_key)
                );
                CREATE INDEX IF NOT EXISTS strategy_mx_orders_open_idx
                    ON strategy_mx_orders(market_id, strategy, action, status);
                CREATE INDEX IF NOT EXISTS strategy_mx_orders_strategy_idx
                    ON strategy_mx_orders(strategy, id DESC);
                CREATE TABLE IF NOT EXISTS strategy_mx_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    strategy TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('ENTRY', 'EXIT')),
                    stage_key TEXT NOT NULL,
                    side TEXT NOT NULL CHECK (side IN ('UP', 'DOWN')),
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    gross REAL NOT NULL,
                    fee REAL NOT NULL,
                    event_sequence TEXT NOT NULL,
                    received_monotonic_ns INTEGER,
                    timestamp TEXT NOT NULL,
                    reason TEXT,
                    UNIQUE(order_id, event_sequence),
                    FOREIGN KEY(order_id) REFERENCES strategy_mx_orders(id)
                );
                CREATE INDEX IF NOT EXISTS strategy_mx_fills_market_idx
                    ON strategy_mx_fills(market_id, strategy, id DESC);
                CREATE INDEX IF NOT EXISTS strategy_mx_fills_strategy_idx
                    ON strategy_mx_fills(strategy, id DESC);
                """
            )
            basis_columns = {
                row["name"]
                for row in self.db.execute(
                    "PRAGMA table_info(strategy_m_basis_samples)"
                ).fetchall()
            }
            if "id" not in basis_columns:
                self.db.executescript(
                    """
                    ALTER TABLE strategy_m_basis_samples
                        RENAME TO strategy_m_basis_samples_legacy;
                    CREATE TABLE strategy_m_basis_samples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market_id INTEGER NOT NULL UNIQUE,
                        captured_at TEXT NOT NULL,
                        start_price REAL NOT NULL,
                        spot_price REAL NOT NULL,
                        basis_bps REAL NOT NULL,
                        signal_received_monotonic_ns INTEGER,
                        signal_event_sequence TEXT,
                        source TEXT NOT NULL
                    );
                    INSERT OR IGNORE INTO strategy_m_basis_samples(
                        market_id, captured_at, start_price, spot_price,
                        basis_bps, signal_received_monotonic_ns,
                        signal_event_sequence, source
                    )
                    SELECT market_id, captured_at, start_price, spot_price,
                           basis_bps, signal_received_monotonic_ns,
                           signal_event_sequence, source
                    FROM strategy_m_basis_samples_legacy
                    ORDER BY captured_at, market_id;
                    DROP TABLE strategy_m_basis_samples_legacy;
                    """
                )
            mx_position_columns = {
                row["name"]
                for row in self.db.execute(
                    "PRAGMA table_info(strategy_mx_positions)"
                ).fetchall()
            }
            if "rev_bid_armed" not in mx_position_columns:
                self.db.execute(
                    """ALTER TABLE strategy_mx_positions
                       ADD COLUMN rev_bid_armed INTEGER NOT NULL DEFAULT 0"""
                )
            if "rev_spot_armed" not in mx_position_columns:
                self.db.execute(
                    """ALTER TABLE strategy_mx_positions
                       ADD COLUMN rev_spot_armed INTEGER NOT NULL DEFAULT 0"""
                )
            reset_columns = {
                row["name"]
                for row in self.db.execute(
                    "PRAGMA table_info(strategy_measurement_resets)"
                ).fetchall()
            }
            if "cutoff_trade_id" not in reset_columns:
                legacy_resets = self.db.execute(
                    "SELECT strategy, reset_at FROM strategy_measurement_resets"
                ).fetchall()
                cutoff_trade_id = int(
                    self.db.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0]
                )
                self.db.execute(
                    "ALTER TABLE strategy_measurement_resets RENAME TO strategy_measurement_resets_legacy"
                )
                self.db.execute(
                    """CREATE TABLE strategy_measurement_resets (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           strategy TEXT NOT NULL,
                           cutoff_trade_id INTEGER NOT NULL,
                           reset_at TEXT NOT NULL
                       )"""
                )
                self.db.executemany(
                    """INSERT INTO strategy_measurement_resets(
                           strategy, cutoff_trade_id, reset_at
                       ) VALUES (?, ?, ?)""",
                    [
                        (row["strategy"], cutoff_trade_id, row["reset_at"])
                        for row in legacy_resets
                    ],
                )
                self.db.execute("DROP TABLE strategy_measurement_resets_legacy")
            self.db.execute(
                """CREATE INDEX IF NOT EXISTS strategy_measurement_resets_latest_idx
                   ON strategy_measurement_resets(strategy, id DESC)"""
            )
            observation_columns = {
                row["name"] for row in self.db.execute("PRAGMA table_info(observations)").fetchall()
            }
            for column in (
                "up_ask_size", "up_bid_size", "down_ask_size", "down_bid_size", "book_skew_ms",
                "up_book_timestamp_ms", "down_book_timestamp_ms", "book_age_ms",
                "spot_age_ms", "futures_price", "futures_timestamp_ms", "futures_age_ms",
            ):
                if column not in observation_columns:
                    self.db.execute(f"ALTER TABLE observations ADD COLUMN {column} REAL")
            if "futures_agg_trade_id" not in observation_columns:
                self.db.execute(
                "ALTER TABLE observations ADD COLUMN futures_agg_trade_id INTEGER"
                )
            pair_trade_columns = {
                row["name"]
                for row in self.db.execute(
                    "PRAGMA table_info(strategy_pair_arb_trades)"
                ).fetchall()
            }
            for column, declaration in {
                "requested_shares": "REAL NOT NULL DEFAULT 0",
                "fill_ratio": "REAL NOT NULL DEFAULT 0",
                "partial_fill": "INTEGER NOT NULL DEFAULT 0",
                "up_fill_vwap": "REAL",
                "down_fill_vwap": "REAL",
                "up_levels_consumed": "INTEGER NOT NULL DEFAULT 0",
                "down_levels_consumed": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if column not in pair_trade_columns:
                    self.db.execute(
                        f"ALTER TABLE strategy_pair_arb_trades "
                        f"ADD COLUMN {column} {declaration}"
                    )
            pair_stats_columns = {
                row["name"]
                for row in self.db.execute(
                    "PRAGMA table_info(strategy_pair_arb_market_stats)"
                ).fetchall()
            }
            if "eligible_qc_015_snapshots" not in pair_stats_columns:
                self.db.execute(
                    """ALTER TABLE strategy_pair_arb_market_stats
                       ADD COLUMN eligible_qc_015_snapshots
                       INTEGER NOT NULL DEFAULT 0"""
                )
            trade_columns = {
                row["name"] for row in self.db.execute("PRAGMA table_info(trades)").fetchall()
            }
            trade_migrations = {
                "fee_rate_bps": "INTEGER NOT NULL DEFAULT 200",
                "strategy_version": "TEXT",
                "model_probability": "REAL",
                "model_edge": "REAL",
                "model_sigma": "REAL",
                "diagnostics_json": "TEXT",
            }
            for column, declaration in trade_migrations.items():
                if column not in trade_columns:
                    self.db.execute(f"ALTER TABLE trades ADD COLUMN {column} {declaration}")
            settlement_columns = {
                row["name"]
                for row in self.db.execute("PRAGMA table_info(market_settlements)").fetchall()
            }
            if "h_processed" not in settlement_columns:
                self.db.execute(
                    "ALTER TABLE market_settlements ADD COLUMN h_processed INTEGER NOT NULL DEFAULT 0"
                )
            for key, value in DEFAULT_CONFIG.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO config(key, value) VALUES (?, ?)",
                    (key, float(value)),
                )
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_h_state(
                       id, mode, reversal_streak, loss_streak,
                       last_processed_market_id, armed_at, updated_at
                   ) VALUES (1, 'DETECTING', 0, 0, NULL, NULL, ?)""",
                (utc_iso(),),
            )
            # E2 was not live while its pre-release 120-sample default existed.
            # Upgrade only that untouched default; preserve any value once E2 has traded.
            self.db.execute(
                """UPDATE config SET value=300
                   WHERE key='strategy_e2_lookback_observations' AND value=120
                     AND NOT EXISTS (SELECT 1 FROM trades WHERE strategy='E2')"""
            )
            boundary = self.db.execute(
                "SELECT forward_started_at FROM confirmation_add_shadow_meta WHERE singleton=1"
            ).fetchone()
            if boundary is None:
                forward_started_at = utc_iso()
                self.db.execute(
                    "INSERT INTO confirmation_add_shadow_meta VALUES (1, ?)",
                    (forward_started_at,),
                )
                self.db.execute(
                    """UPDATE confirmation_add_shadow_state
                          SET status='EXCLUDED_PREDEPLOY', updated_at=?
                        WHERE status IN ('ACTIVE', 'OFFICIAL')""",
                    (forward_started_at,),
                )
            self._recalculate_trade_accounting()
            self.db.commit()

    def _recalculate_trade_accounting(self) -> None:
        """Repair old rows that used a flat percentage-of-notional fee model."""
        trades = self.db.execute("SELECT * FROM trades").fetchall()
        for trade in trades:
            shares = float(trade["shares"])
            entry_price = float(trade["entry_price"])
            fee_bps = int(trade["fee_rate_bps"])
            total_fees = taker_fee(shares, entry_price, fee_bps)
            pnl = trade["pnl"]
            status = str(trade["status"])
            exit_price = trade["exit_price"]

            if status in {"TARGET_FILLED", "TIMEOUT_EXIT", "STOP_LOSS_EXIT"} and exit_price is not None:
                exit_price = float(exit_price)
                total_fees += taker_fee(shares, exit_price, fee_bps)
                pnl = shares * exit_price - float(trade["stake"]) - total_fees
            elif status in {"PAIRED_LOCKED", "PAIR_LOCKED"} and exit_price is not None:
                exit_price = float(exit_price)
                total_fees += taker_fee(shares, exit_price, fee_bps)
                pnl = shares - float(trade["stake"]) - shares * exit_price - total_fees
            elif status in {"SETTLED_WIN", "SETTLED_LOSS"}:
                payout = shares if status == "SETTLED_WIN" else 0.0
                pnl = payout - float(trade["stake"]) - total_fees

            self.db.execute(
                "UPDATE trades SET fees=?, pnl=? WHERE id=?",
                (total_fees, pnl, trade["id"]),
            )

    def config(self) -> dict[str, float | bool]:
        with self.lock:
            if self._read_only or self._config_cache is None:
                rows = self.db.execute("SELECT key, value FROM config").fetchall()
                values = {
                    row["key"]: (
                        bool(row["value"])
                        if row["key"].endswith("_enabled")
                        else row["value"]
                    )
                    for row in rows
                }
                if not self._read_only:
                    self._config_cache = values
                return dict(values)
            return dict(self._config_cache)

    def update_config(self, values: dict[str, Any]) -> dict[str, float | bool]:
        allowed = set(DEFAULT_CONFIG)
        normalized: dict[str, float | bool] = {}
        for key, raw in values.items():
            if key not in allowed:
                continue
            value: float | bool = bool(raw) if key.endswith("_enabled") else float(raw)
            if not key.endswith("_enabled") and float(value) < 0:
                raise ValueError(f"{key} cannot be negative")
            normalized[key] = value
        candidate = self.config()
        candidate.update(normalized)
        self._validate_config(candidate)
        with self.lock:
            for key, value in normalized.items():
                self.db.execute("UPDATE config SET value=? WHERE key=?", (value, key))
            self.db.commit()
            self._config_cache = dict(candidate)
            return dict(self._config_cache)

    def reset_strategy_measurement(self, strategy: str) -> dict[str, Any]:
        """Start a new summary measurement window without altering trade history."""
        if not isinstance(strategy, str):
            raise ValueError("strategy must be one of the supported strategy names")
        normalized = strategy.strip().upper()
        if normalized not in SUPPORTED_STRATEGIES:
            raise ValueError(f"unsupported strategy: {strategy}")
        reset_at = utc_iso()
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                cutoff_trade_id = int(
                    self.db.execute("SELECT COALESCE(MAX(id), 0) FROM trades").fetchone()[0]
                )
                self.db.execute(
                    """INSERT INTO strategy_measurement_resets(
                           strategy, cutoff_trade_id, reset_at
                       ) VALUES (?, ?, ?)""",
                    (normalized, cutoff_trade_id, reset_at),
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {
            "strategy": normalized,
            "resetAt": reset_at,
            "cutoffTradeId": cutoff_trade_id,
        }

    @staticmethod
    def _validate_config(config: dict[str, float | bool]) -> None:
        finite_keys = [
            "strategy_l_window_seconds",
            "strategy_l_max_entry",
            "strategy_l_target",
            "strategy_l_max_book_skew_ms",
            "strategy_l_max_book_age_ms",
            "strategy_l_total_stake",
            "strategy_m_entry_window_seconds",
            "strategy_m_stake",
            "strategy_m_max_book_skew_ms",
            "strategy_m_max_book_age_ms",
        ]
        for prefix in ("m0", "m01", "m01o", "m01f", "m01r", "m0w", "m01w", "m1", "m2", "m3", "m4", "m5", "m6"):
            finite_keys.extend(
                (
                    f"strategy_{prefix}_entry_window_seconds",
                    f"strategy_{prefix}_stake",
                    f"strategy_{prefix}_max_book_skew_ms",
                    f"strategy_{prefix}_max_book_age_ms",
                )
            )
        finite_keys.extend(
            (
                "strategy_m0_seed",
                "strategy_m01_max_entry",
                "strategy_m01t180_min_seconds_left",
                "strategy_m01t180_max_entry",
                "strategy_m01t180_stake",
                "strategy_m01t180_max_book_skew_ms",
                "strategy_m01t180_max_book_age_ms",
                "strategy_m01o_max_entry",
                "strategy_m01o_min_observer_samples",
                "strategy_m01o_min_current_range_score",
                "strategy_m01f_min_entry",
                "strategy_m01f_max_entry",
                "strategy_m01r_max_anchor",
                "strategy_m01r_rebound",
                "strategy_m01w_max_entry",
                "strategy_m3_min_abs_delta_bps",
                "strategy_m4_required_observations",
                "strategy_m6_min_basis_samples",
                "strategy_m7_entry_window_seconds",
                "strategy_m7_stake",
                "strategy_m7_max_book_skew_ms",
                "strategy_m7_max_book_age_ms",
                "strategy_m7_execution_grace_seconds",
                "strategy_pair_arb_stake",
                "strategy_pair_arb_max_book_skew_ms",
                "strategy_pair_arb_max_book_age_ms",
            )
        )
        for key in finite_keys:
            if not math.isfinite(float(config[key])):
                raise ValueError(f"{key} must be finite")
        bounded_prices = (
            "strategy_a_min_gap", "strategy_a_max_entry", "strategy_a_target",
            "strategy_b_min_price", "strategy_b_max_price",
            "strategy_b_stop_loss_price",
            "strategy_b2_min_price", "strategy_b2_max_price",
            "strategy_b2_stop_loss_price",
            "strategy_c_min_gap", "strategy_c_max_entry",
            "strategy_d_min_gap", "strategy_d_max_first_entry", "strategy_d_max_pair_cost",
            "strategy_e_min_entry", "strategy_e_max_entry", "strategy_e_max_spread",
            "strategy_f_min_gap", "strategy_f_max_first_entry", "strategy_f_max_pair_cost",
            "strategy_e2_min_entry", "strategy_e2_max_entry", "strategy_e2_max_spread",
            "strategy_g_min_entry", "strategy_g_max_entry", "strategy_g_max_spread",
            "strategy_g_probability_shrinkage", "strategy_g_uncertainty_margin",
            "strategy_g_min_net_edge",
            "strategy_h_leader_min_price", "strategy_h_max_entry",
            "strategy_i_max_entry",
            "strategy_j_min_gap", "strategy_j_max_entry",
            "strategy_k_min_probability", "strategy_k_max_spread",
            "strategy_k_min_net_edge",
            "strategy_l_max_entry", "strategy_l_target", "strategy_m01_max_entry",
            "strategy_m01t180_max_entry",
            "strategy_m01o_max_entry",
            "strategy_m01f_min_entry", "strategy_m01f_max_entry",
            "strategy_m01r_max_anchor", "strategy_m01r_rebound",
            "strategy_m01w_max_entry",
        )
        for key in bounded_prices:
            if float(config[key]) > 1:
                raise ValueError(f"{key} cannot exceed 1")
        if float(config["strategy_b_min_price"]) > float(config["strategy_b_max_price"]):
            raise ValueError("strategy B minimum price cannot exceed maximum price")
        if float(config["strategy_b_stop_loss_price"]) >= float(
            config["strategy_b_min_price"]
        ):
            raise ValueError("strategy B stop loss must be below its minimum entry price")
        if float(config["strategy_b2_min_price"]) > float(config["strategy_b2_max_price"]):
            raise ValueError("strategy B2 minimum price cannot exceed maximum price")
        if float(config["strategy_b2_stop_loss_price"]) >= float(
            config["strategy_b2_min_price"]
        ):
            raise ValueError("strategy B2 stop loss must be below its minimum entry price")
        if float(config["strategy_b2_min_stake"]) > float(config["strategy_b2_max_stake"]):
            raise ValueError("strategy B2 minimum stake cannot exceed maximum stake")
        if float(config["strategy_m01f_min_entry"]) > float(
            config["strategy_m01f_max_entry"]
        ):
            raise ValueError("strategy M01-Floor minimum price cannot exceed maximum price")
        if float(config["strategy_m01r_max_anchor"]) + float(
            config["strategy_m01r_rebound"]
        ) > 1:
            raise ValueError("strategy M01-Rebound anchor plus rebound cannot exceed 1")
        if float(config["strategy_b2_min_seconds_left"]) >= float(
            config["strategy_b2_last_seconds"]
        ):
            raise ValueError("strategy B2 minimum time must be less than its window")
        if float(config["strategy_e_min_entry"]) > float(config["strategy_e_max_entry"]):
            raise ValueError("strategy E minimum entry cannot exceed maximum entry")
        if float(config["strategy_e_min_seconds_left"]) > float(config["strategy_e_max_seconds_left"]):
            raise ValueError("strategy E time window is reversed")
        for strategy in ("e2", "g"):
            if float(config[f"strategy_{strategy}_min_entry"]) > float(
                config[f"strategy_{strategy}_max_entry"]
            ):
                raise ValueError(f"strategy {strategy.upper()} minimum price cannot exceed maximum price")
            if float(config[f"strategy_{strategy}_min_seconds_left"]) > float(
                config[f"strategy_{strategy}_max_seconds_left"]
            ):
                raise ValueError(f"strategy {strategy.upper()} time window is reversed")
        for key in (
            "strategy_a_stake", "strategy_b_stake", "strategy_c_stake",
            "strategy_b2_min_stake", "strategy_b2_max_stake",
            "strategy_d_stake", "strategy_e_stake", "strategy_f_stake",
            "strategy_e2_stake", "strategy_g_stake",
            "strategy_h_stake", "strategy_i_stake", "strategy_j_stake",
            "strategy_k_stake", "strategy_l_total_stake", "strategy_m_stake",
            "strategy_m0_stake", "strategy_m1_stake", "strategy_m2_stake",
            "strategy_m01_stake", "strategy_m01t180_stake", "strategy_m01o_stake", "strategy_m01f_stake", "strategy_m01r_stake",
            "strategy_m0w_stake",
            "strategy_m01w_stake",
            "strategy_m3_stake", "strategy_m4_stake", "strategy_m5_stake",
            "strategy_m6_stake", "strategy_m7_stake",
            "strategy_pair_arb_stake",
        ):
            if float(config[key]) <= 0:
                raise ValueError(f"{key} must be positive")
        for key in (
            "strategy_e2_sigma_floor", "strategy_g_sigma_floor",
            "strategy_e2_min_return_samples", "strategy_g_min_return_samples",
            "strategy_e2_lookback_observations", "strategy_g_lookback_observations",
            "strategy_e2_max_book_age_ms", "strategy_g_max_book_age_ms",
            "strategy_h_max_book_skew_ms", "strategy_h_max_book_age_ms",
            "strategy_i_max_book_skew_ms", "strategy_i_max_book_age_ms",
            "strategy_j_max_book_skew_ms", "strategy_j_max_book_age_ms",
            "strategy_k_sigma_floor", "strategy_k_basis_uncertainty_bps",
            "strategy_k_calibration_slope", "strategy_k_min_baseline_seconds_left",
            "strategy_k_min_return_samples", "strategy_k_lookback_observations",
            "strategy_k_spread_edge_multiplier", "strategy_k_max_book_skew_ms",
            "strategy_k_max_book_age_ms",
            "strategy_b2_last_seconds", "strategy_b2_min_seconds_left",
            "strategy_b2_size_curve",
            "strategy_l_window_seconds", "strategy_l_max_book_skew_ms",
            "strategy_l_max_book_age_ms",
            "strategy_m_entry_window_seconds", "strategy_m_max_book_skew_ms",
            "strategy_m_max_book_age_ms",
            "strategy_m0_entry_window_seconds", "strategy_m0_max_book_skew_ms",
            "strategy_m0_max_book_age_ms",
            "strategy_m01_entry_window_seconds", "strategy_m01_max_book_skew_ms",
            "strategy_m01_max_book_age_ms",
            "strategy_m01t180_min_seconds_left", "strategy_m01t180_max_book_skew_ms",
            "strategy_m01t180_max_book_age_ms",
            "strategy_m01o_entry_window_seconds", "strategy_m01o_max_book_skew_ms",
            "strategy_m01o_max_book_age_ms",
            "strategy_m01f_entry_window_seconds", "strategy_m01f_max_book_skew_ms",
            "strategy_m01f_max_book_age_ms",
            "strategy_m01r_entry_window_seconds", "strategy_m01r_max_book_skew_ms",
            "strategy_m01r_max_book_age_ms",
            "strategy_m0w_entry_window_seconds", "strategy_m0w_max_book_skew_ms",
            "strategy_m0w_max_book_age_ms",
            "strategy_m01w_entry_window_seconds", "strategy_m01w_max_book_skew_ms",
            "strategy_m01w_max_book_age_ms",
            "strategy_m1_entry_window_seconds", "strategy_m1_max_book_skew_ms",
            "strategy_m1_max_book_age_ms",
            "strategy_m2_entry_window_seconds", "strategy_m2_max_book_skew_ms",
            "strategy_m2_max_book_age_ms",
            "strategy_m3_entry_window_seconds", "strategy_m3_max_book_skew_ms",
            "strategy_m3_max_book_age_ms", "strategy_m3_min_abs_delta_bps",
            "strategy_m4_entry_window_seconds", "strategy_m4_max_book_skew_ms",
            "strategy_m4_max_book_age_ms", "strategy_m4_required_observations",
            "strategy_m5_entry_window_seconds", "strategy_m5_max_book_skew_ms",
            "strategy_m5_max_book_age_ms",
            "strategy_m6_entry_window_seconds", "strategy_m6_max_book_skew_ms",
            "strategy_m6_max_book_age_ms", "strategy_m6_min_basis_samples",
            "strategy_m7_entry_window_seconds", "strategy_m7_max_book_skew_ms",
            "strategy_m7_max_book_age_ms", "strategy_m7_execution_grace_seconds",
            "strategy_pair_arb_max_book_skew_ms", "strategy_pair_arb_max_book_age_ms",
        ):
            if float(config[key]) <= 0:
                raise ValueError(f"{key} must be positive")
        if float(config["strategy_g_slippage_bps"]) > 10_000:
            raise ValueError("strategy G slippage cannot exceed 10000 bps")
        if float(config["strategy_k_slippage_bps"]) > 10_000:
            raise ValueError("strategy K slippage cannot exceed 10000 bps")
        for key in (
            "strategy_h_reversal_seconds", "strategy_h_entry_window_seconds",
            "strategy_h_anchor_lookback_seconds",
            "strategy_h_max_entry", "strategy_h_required_reversals",
            "strategy_h_max_consecutive_losses",
            "strategy_i_max_entry",
            "strategy_j_window_seconds", "strategy_j_max_entry",
            "strategy_j_target_multiplier",
        ):
            if float(config[key]) <= 0:
                raise ValueError(f"{key} must be positive")
        if float(config["strategy_j_min_seconds_left"]) >= float(
            config["strategy_j_window_seconds"]
        ):
            raise ValueError("strategy J minimum time must be less than its window")
        if float(config["strategy_k_min_seconds_left"]) > float(
            config["strategy_k_max_seconds_left"]
        ):
            raise ValueError("strategy K time window is reversed")
        if float(config["strategy_k_min_baseline_seconds_left"]) < float(
            config["strategy_k_max_seconds_left"]
        ):
            raise ValueError("strategy K baseline must precede its entry window")
        if not 0.5 <= float(config["strategy_k_min_probability"]) <= 1.0:
            raise ValueError("strategy K minimum probability must be between 0.5 and 1")
        if float(config["strategy_k_min_return_samples"]) >= float(
            config["strategy_k_lookback_observations"]
        ):
            raise ValueError("strategy K return samples must be less than lookback observations")
        if float(config["strategy_l_target"]) <= float(config["strategy_l_max_entry"]):
            raise ValueError("strategy L target must exceed its maximum entry price")
        if float(config["strategy_l_max_entry"]) <= 0:
            raise ValueError("strategy L maximum entry price must be positive")
        if float(config["strategy_m01_max_entry"]) <= 0:
            raise ValueError("strategy M01 maximum entry price must be positive")
        if float(config["strategy_m01t180_max_entry"]) <= 0:
            raise ValueError("strategy M01T180 maximum entry price must be positive")
        if not 0 < float(config["strategy_m01t180_min_seconds_left"]) < 300:
            raise ValueError("strategy M01T180 minimum seconds left must be between 0 and 300")
        if float(config["strategy_m01o_max_entry"]) <= 0:
            raise ValueError("strategy M01O maximum entry price must be positive")
        if float(config["strategy_m01f_min_entry"]) <= 0:
            raise ValueError("strategy M01-Floor minimum entry price must be positive")
        if float(config["strategy_m01f_max_entry"]) <= 0:
            raise ValueError("strategy M01-Floor maximum entry price must be positive")
        if float(config["strategy_m01r_max_anchor"]) <= 0:
            raise ValueError("strategy M01-Rebound maximum anchor must be positive")
        if float(config["strategy_m01r_rebound"]) <= 0:
            raise ValueError("strategy M01-Rebound amount must be positive")
        if float(config["strategy_m01w_max_entry"]) <= 0:
            raise ValueError("strategy M01W maximum entry price must be positive")
        if float(config["strategy_l_window_seconds"]) > 300:
            raise ValueError("strategy L entry window cannot exceed 300 seconds")
        if float(config["strategy_m_entry_window_seconds"]) > 300:
            raise ValueError("strategy M entry window cannot exceed 300 seconds")
        for prefix in ("m0", "m01", "m01o", "m01f", "m01r", "m0w", "m01w", "m1", "m2", "m3", "m4", "m5", "m6", "m7"):
            if float(config[f"strategy_{prefix}_entry_window_seconds"]) > 300:
                raise ValueError(
                    f"strategy {prefix.upper()} entry window cannot exceed 300 seconds"
                )
        if not float(config["strategy_m0_seed"]).is_integer():
            raise ValueError("strategy M0 seed must be a whole number")
        if not float(config["strategy_m01o_min_observer_samples"]).is_integer():
            raise ValueError("strategy M01O observer samples must be a whole number")
        if not 6 <= int(config["strategy_m01o_min_observer_samples"]) <= 20:
            raise ValueError("strategy M01O observer samples must be between 6 and 20")
        if not float(config["strategy_m01o_min_current_range_score"]).is_integer():
            raise ValueError("strategy M01O current range score must be a whole number")
        if not 1 <= int(config["strategy_m01o_min_current_range_score"]) <= 4:
            raise ValueError("strategy M01O current range score must be between 1 and 4")
        if not float(config["strategy_m4_required_observations"]).is_integer():
            raise ValueError("strategy M4 required observations must be a whole number")
        if float(config["strategy_m4_required_observations"]) < 2:
            raise ValueError("strategy M4 requires at least two observations")
        if not float(config["strategy_m6_min_basis_samples"]).is_integer():
            raise ValueError("strategy M6 minimum basis samples must be a whole number")
        if float(config["strategy_m6_min_basis_samples"]) < 2:
            raise ValueError("strategy M6 requires at least two prior markets")
        regime_direction_mode = float(
            config["strategy_r_futures_lead_regime_reverse_3l_direction_mode"]
        )
        if not regime_direction_mode.is_integer() or int(regime_direction_mode) not in {
            0,
            1,
            2,
        }:
            raise ValueError(
                "R_FUTURES_LEAD_REGIME_REVERSE_3L direction mode must be "
                "0 (AUTO), 1 (FORWARD), or 2 (REVERSE)"
            )
        for key in (
            "strategy_h_required_reversals", "strategy_h_max_consecutive_losses",
            "strategy_k_min_return_samples", "strategy_k_lookback_observations",
        ):
            if not float(config[key]).is_integer():
                raise ValueError(f"{key} must be a whole number")
        research_stakes = [
            float(config[f"strategy_{strategy.lower()}_stake"])
            for strategy in RESEARCH_STRATEGIES
        ]
        research_safety_keys = (
            "strategy_research_shared_cap_usdt",
            "strategy_research_min_stake_usdt",
            "strategy_research_slippage_bps",
            "strategy_research_min_entry",
            "strategy_research_max_spread",
            "strategy_research_max_book_skew_ms",
            "strategy_research_max_book_age_ms",
        )
        if not all(math.isfinite(float(config[key])) for key in research_safety_keys):
            raise ValueError("research strategy safety settings must be finite")
        minimum_stake = float(config["strategy_research_min_stake_usdt"])
        shared_cap = float(config["strategy_research_shared_cap_usdt"])
        if minimum_stake < 1.5:
            raise ValueError("research strategy minimum stake cannot be below 1.5 USDT")
        if shared_cap <= 0 or any(stake < minimum_stake for stake in research_stakes):
            raise ValueError("research strategy stakes must meet the configured minimum")
        if any(stake > shared_cap for stake in research_stakes):
            raise ValueError("a research strategy stake cannot exceed the shared capital cap")
        if not 0 < float(config["strategy_research_min_entry"]) < 1:
            raise ValueError("research strategy minimum entry must be between 0 and 1")
        if not 0 <= float(config["strategy_research_max_spread"]) <= 1:
            raise ValueError("research strategy maximum spread must be between 0 and 1")
        if not 0 <= float(config["strategy_research_slippage_bps"]) <= 10_000:
            raise ValueError("research strategy slippage must be between 0 and 10000 bps")
        if float(config["strategy_research_max_book_skew_ms"]) <= 0 or float(
            config["strategy_research_max_book_age_ms"]
        ) <= 0:
            raise ValueError("research strategy book freshness limits must be positive")

    def observe(self, row: dict[str, Any]) -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO observations(
                    timestamp, topic_id, market_id, title, start_price, spot_price,
                    spot_age_ms,
                    futures_price, futures_timestamp_ms, futures_age_ms,
                    futures_agg_trade_id,
                    seconds_left, up_ask, up_bid, down_ask, down_bid,
                    up_ask_size, up_bid_size, down_ask_size, down_bid_size, book_skew_ms,
                    up_book_timestamp_ms, down_book_timestamp_ms, book_age_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(row.get(key) for key in (
                    "timestamp", "topic_id", "market_id", "title", "start_price", "spot_price",
                    "spot_age_ms",
                    "futures_price", "futures_timestamp_ms", "futures_age_ms",
                    "futures_agg_trade_id",
                    "seconds_left", "up_ask", "up_bid", "down_ask", "down_bid",
                    "up_ask_size", "up_bid_size", "down_ask_size", "down_bid_size", "book_skew_ms",
                    "up_book_timestamp_ms", "down_book_timestamp_ms", "book_age_ms",
                )),
            )
            self.db.commit()

    @staticmethod
    def _m_snapshot_schedule(
        snapshot: dict[str, Any],
    ) -> tuple[int | None, int | None]:
        """Return the exchange-supplied five-minute schedule when available."""
        try:
            start_ms = int(snapshot["market_start_ms"])
            end_ms = int(snapshot["market_end_ms"])
        except (KeyError, TypeError, ValueError):
            return None, None
        if start_ms <= 0 or end_ms <= start_ms:
            return None, None
        return start_ms, end_ms

    @staticmethod
    def _m_observation_schedule(
        observation: sqlite3.Row | None,
    ) -> tuple[int | None, int | None]:
        """Reconstruct the scheduled boundary from a persisted REST sample.

        REST timestamps include local/network skew, so the inferred end is
        rounded to the nearest five-minute boundary before adjacency checks.
        """
        if observation is None:
            return None, None
        try:
            observed_at = datetime.fromisoformat(str(observation["timestamp"]))
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            raw_end_ms = (
                observed_at.timestamp() * 1_000
                + float(observation["seconds_left"]) * 1_000
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, None
        rounded_end_ms = int(
            (raw_end_ms + M_MARKET_DURATION_MS / 2)
            // M_MARKET_DURATION_MS
            * M_MARKET_DURATION_MS
        )
        return rounded_end_ms - M_MARKET_DURATION_MS, rounded_end_ms

    def _register_m_market_sequence(
        self, snapshot: dict[str, Any]
    ) -> sqlite3.Row:
        """Persist the exact predecessor for M0W/M01W win-gating.

        Market IDs are not consecutive and official settlement can arrive
        after the next market opens.  Therefore settlement recency cannot
        identify the previous round.  The realtime exchange schedule is the
        canonical source; persisted observations provide restart recovery.
        """
        market_id = int(snapshot["market_id"])
        topic_id = int(snapshot["topic_id"])
        start_ms, end_ms = self._m_snapshot_schedule(snapshot)
        existing = self.db.execute(
            "SELECT * FROM strategy_m_market_sequence WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if existing is not None:
            if (
                (existing["start_ms"] is None and start_ms is not None)
                or (existing["end_ms"] is None and end_ms is not None)
            ):
                self.db.execute(
                    """UPDATE strategy_m_market_sequence
                       SET start_ms=COALESCE(start_ms, ?),
                           end_ms=COALESCE(end_ms, ?)
                       WHERE market_id=?""",
                    (start_ms, end_ms, market_id),
                )
                existing = self.db.execute(
                    "SELECT * FROM strategy_m_market_sequence WHERE market_id=?",
                    (market_id,),
                ).fetchone()
            assert existing is not None
            return existing

        previous: sqlite3.Row | None = None
        previous_start_ms: int | None = None
        previous_end_ms: int | None = None

        # Prefer an already registered exchange schedule.  This is the normal
        # rollover path and remains correct even before the 1 s REST collector
        # has persisted its first sample for the new market.
        if start_ms is not None:
            scheduled = self.db.execute(
                """SELECT * FROM strategy_m_market_sequence
                   WHERE market_id<>? AND start_ms IS NOT NULL AND start_ms<?
                   ORDER BY start_ms DESC, sequence_no DESC LIMIT 1""",
                (market_id, start_ms),
            ).fetchone()
            if scheduled is not None and scheduled["end_ms"] is not None:
                scheduled_end_ms = int(scheduled["end_ms"])
                if abs(scheduled_end_ms - start_ms) <= M_MARKET_ADJACENCY_TOLERANCE_MS:
                    previous = scheduled
                    previous_start_ms = int(scheduled["start_ms"])
                    previous_end_ms = scheduled_end_ms

        # On the first run after this migration/restart, recover the immediately
        # preceding observed market and validate its rounded boundary.
        if previous is None:
            first_observation = self.db.execute(
                "SELECT MIN(id) FROM observations WHERE market_id=?",
                (market_id,),
            ).fetchone()[0]
            if first_observation is not None:
                observed_previous = self.db.execute(
                    """SELECT market_id, topic_id, timestamp, seconds_left
                       FROM observations
                       WHERE id<? AND market_id<>?
                       ORDER BY id DESC LIMIT 1""",
                    (int(first_observation), market_id),
                ).fetchone()
            else:
                observed_previous = self.db.execute(
                    """SELECT market_id, topic_id, timestamp, seconds_left
                       FROM observations WHERE market_id<>?
                       ORDER BY id DESC LIMIT 1""",
                    (market_id,),
                ).fetchone()
            observed_start_ms, observed_end_ms = self._m_observation_schedule(
                observed_previous
            )
            observation_is_adjacent = bool(
                observed_previous is not None
                and (
                    start_ms is None
                    or (
                        observed_end_ms is not None
                        and abs(observed_end_ms - start_ms)
                        <= M_MARKET_ADJACENCY_TOLERANCE_MS
                    )
                )
            )
            if observation_is_adjacent and observed_previous is not None:
                previous = observed_previous
                previous_start_ms = observed_start_ms
                previous_end_ms = observed_end_ms

        # Direct simulations/tests may not carry an exchange schedule or REST
        # samples.  In that isolated case, invocation order is the only clock.
        if previous is None and start_ms is None:
            previous = self.db.execute(
                """SELECT * FROM strategy_m_market_sequence
                   WHERE market_id<>? ORDER BY sequence_no DESC LIMIT 1""",
                (market_id,),
            ).fetchone()
            if previous is not None:
                previous_start_ms = (
                    int(previous["start_ms"])
                    if previous["start_ms"] is not None else None
                )
                previous_end_ms = (
                    int(previous["end_ms"])
                    if previous["end_ms"] is not None else None
                )

        previous_market_id = (
            int(previous["market_id"]) if previous is not None else None
        )
        if previous_market_id is not None:
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_m_market_sequence(
                       market_id, topic_id, start_ms, end_ms,
                       previous_market_id, previous_is_adjacent, first_seen_at
                   ) VALUES (?, ?, ?, ?, NULL, 0, ?)""",
                (
                    previous_market_id,
                    int(previous["topic_id"]),
                    previous_start_ms,
                    previous_end_ms,
                    str(previous["timestamp"])
                    if "timestamp" in previous.keys()
                    else utc_iso(),
                ),
            )
        self.db.execute(
            """INSERT INTO strategy_m_market_sequence(
                   market_id, topic_id, start_ms, end_ms,
                   previous_market_id, previous_is_adjacent, first_seen_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                topic_id,
                start_ms,
                end_ms,
                previous_market_id,
                1 if previous_market_id is not None else 0,
                utc_iso(),
            ),
        )
        if previous_market_id is not None:
            self.db.execute(
                """UPDATE strategy_m_market_sequence
                   SET start_ms=COALESCE(start_ms, ?),
                       end_ms=COALESCE(end_ms, ?)
                   WHERE market_id=?""",
                (previous_start_ms, previous_end_ms, previous_market_id),
            )
        registered = self.db.execute(
            "SELECT * FROM strategy_m_market_sequence WHERE market_id=?",
            (market_id,),
        ).fetchone()
        assert registered is not None
        return registered

    def has_trade(self, strategy: str, market_id: int) -> bool:
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
                (strategy, market_id),
            ).fetchone()
        return row is not None

    def has_trade_side(self, strategy: str, market_id: int, side: str) -> bool:
        """Return whether a market side has ever been attempted by a strategy."""
        with self.lock:
            row = self.db.execute(
                """SELECT 1 FROM trades
                   WHERE strategy=? AND market_id=? AND side=? LIMIT 1""",
                (strategy, market_id, side),
            ).fetchone()
        return row is not None

    def strategy_h_state(self) -> dict[str, Any]:
        """Return persistent Strategy H state and its latest settlement audit events."""
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM strategy_h_state WHERE id=1"
            ).fetchone()
            events = self.db.execute(
                "SELECT * FROM strategy_h_events ORDER BY id DESC LIMIT 20"
            ).fetchall()
            pending_official = self.db.execute(
                "SELECT COUNT(*) FROM market_settlements WHERE status='PENDING'"
            ).fetchone()[0]
        assert row is not None
        recent_events: list[dict[str, Any]] = []
        for event in events:
            item = dict(event)
            item["official"] = bool(item["official"])
            if item["details_json"]:
                item["details"] = json.loads(item.pop("details_json"))
            else:
                item.pop("details_json", None)
                item["details"] = None
            recent_events.append(item)
        return {
            "mode": row["mode"],
            "reversalStreak": int(row["reversal_streak"]),
            "lossStreak": int(row["loss_streak"]),
            "lastProcessedMarketId": row["last_processed_market_id"],
            "armedAt": row["armed_at"],
            "updatedAt": row["updated_at"],
            "pendingOfficialSettlements": int(pending_official),
            "recentEvents": recent_events,
        }

    def strategy_h_reversal_evidence(
        self,
        market_id: int,
        winner: str,
        cfg: dict[str, float | bool],
    ) -> dict[str, Any] | None:
        """Find the losing leader immediately before the configured reversal window."""
        losing_side = "DOWN" if winner == "UP" else "UP"
        price_column = "up_ask" if losing_side == "UP" else "down_ask"
        with self.lock:
            row = self.db.execute(
                f"""SELECT {price_column} AS price, seconds_left, timestamp
                    FROM observations
                    WHERE market_id=? AND seconds_left > ? AND seconds_left <= ?
                      AND {price_column} >= ?
                      AND book_age_ms IS NOT NULL AND book_age_ms <= ?
                      AND book_skew_ms IS NOT NULL AND book_skew_ms <= ?
                      AND up_ask IS NOT NULL AND up_bid IS NOT NULL
                      AND down_ask IS NOT NULL AND down_bid IS NOT NULL
                      AND up_ask >= up_bid AND down_ask >= down_bid
                    ORDER BY seconds_left ASC, id DESC
                    LIMIT 1""",
                (
                    market_id,
                    float(cfg["strategy_h_reversal_seconds"]),
                    float(cfg["strategy_h_reversal_seconds"])
                    + float(cfg["strategy_h_anchor_lookback_seconds"]),
                    float(cfg["strategy_h_leader_min_price"]),
                    float(cfg["strategy_h_max_book_age_ms"]),
                    float(cfg["strategy_h_max_book_skew_ms"]),
                ),
            ).fetchone()
        if row is None:
            return None
        return {
            "side": losing_side,
            "price": float(row["price"]),
            "seconds_left": float(row["seconds_left"]),
            "timestamp": row["timestamp"],
        }

    def _process_strategy_h_settlement(
        self,
        *,
        market_id: int,
        winner: str,
        official: bool,
        h_result: str | None,
        cfg: dict[str, float | bool],
    ) -> None:
        """Advance Strategy H exactly once for a settled market.

        This method runs inside ``settle_market``'s transaction. Only official
        outcomes can create reversal streaks or count an H win/loss.
        """
        existing_event = self.db.execute(
            "SELECT * FROM strategy_h_events WHERE market_id=?", (market_id,)
        ).fetchone()
        if existing_event is not None and (
            not official or bool(existing_event["official"])
        ):
            return

        state = self.db.execute(
            "SELECT * FROM strategy_h_state WHERE id=1"
        ).fetchone()
        assert state is not None
        mode_before = str(state["mode"])
        mode_after = mode_before
        reversal_streak = int(state["reversal_streak"])
        loss_streak = int(state["loss_streak"])
        armed_at = state["armed_at"]
        evidence: dict[str, Any] | None = None
        details: dict[str, Any] = {
            "hResult": h_result,
            "config": strategy_config_snapshot(cfg, "h"),
        }
        if existing_event is not None and existing_event["details_json"]:
            pending_details = json.loads(existing_event["details_json"])
            if "proxyWinner" in pending_details:
                details["proxyWinner"] = pending_details["proxyWinner"]

        if not official:
            updated_at = utc_iso()
            self.db.execute(
                """INSERT INTO strategy_h_events(
                       timestamp, market_id, event_type, winner, official,
                       candidate_side, candidate_price, candidate_seconds_left,
                       mode_before, mode_after, reversal_streak, loss_streak, details_json
                   ) VALUES (?, ?, 'PENDING_OFFICIAL', ?, 0, NULL, NULL, NULL,
                             ?, ?, ?, ?, ?)""",
                (
                    updated_at, market_id, winner, mode_before, mode_before,
                    reversal_streak, loss_streak,
                    json.dumps(
                        {
                            "proxyWinner": winner,
                            "hResult": h_result,
                            "config": strategy_config_snapshot(cfg, "h"),
                        },
                        sort_keys=True,
                    ),
                ),
            )
            return

        if not cfg["strategy_h_enabled"]:
            event_type = "DISABLED"
        elif mode_before == "ARMED":
            if h_result is None:
                event_type = "ARMED_NO_TRADE"
            elif not official:
                event_type = "H_UNVERIFIED_RESULT"
            elif h_result == "WIN":
                event_type = "H_WIN"
                loss_streak = 0
            else:
                consecutive_losses = loss_streak + 1
                details["consecutiveLosses"] = consecutive_losses
                if consecutive_losses >= int(cfg["strategy_h_max_consecutive_losses"]):
                    event_type = "H_LOSS_DISARMED"
                    mode_after = "DETECTING"
                    reversal_streak = 0
                    loss_streak = 0
                    armed_at = None
                else:
                    event_type = "H_LOSS"
                    loss_streak = consecutive_losses
        else:
            evidence = self.strategy_h_reversal_evidence(market_id, winner, cfg)
            if evidence is None:
                event_type = "REVERSAL_STREAK_RESET" if reversal_streak else "NO_REVERSAL"
                reversal_streak = 0
            else:
                reversal_streak += 1
                if reversal_streak >= int(cfg["strategy_h_required_reversals"]):
                    event_type = "REVERSAL_ARMED"
                    mode_after = "ARMED"
                    loss_streak = 0
                    armed_at = utc_iso()
                else:
                    event_type = "REVERSAL_DETECTED"

        if evidence is not None:
            details["candidateTimestamp"] = evidence["timestamp"]

        updated_at = utc_iso()
        self.db.execute(
            """UPDATE strategy_h_state
               SET mode=?, reversal_streak=?, loss_streak=?,
                   last_processed_market_id=?, armed_at=?, updated_at=?
               WHERE id=1""",
            (
                mode_after, reversal_streak, loss_streak, market_id, armed_at, updated_at,
            ),
        )
        event_values = (
            updated_at,
            event_type,
            winner,
            evidence["side"] if evidence else None,
            evidence["price"] if evidence else None,
            evidence["seconds_left"] if evidence else None,
            mode_before,
            mode_after,
            reversal_streak,
            loss_streak,
            json.dumps(details, sort_keys=True),
        )
        if existing_event is not None:
            self.db.execute(
                """UPDATE strategy_h_events
                   SET timestamp=?, event_type=?, winner=?, official=1,
                       candidate_side=?, candidate_price=?, candidate_seconds_left=?,
                       mode_before=?, mode_after=?, reversal_streak=?, loss_streak=?,
                       details_json=? WHERE market_id=?""",
                (*event_values, market_id),
            )
        else:
            self.db.execute(
                """INSERT INTO strategy_h_events(
                       timestamp, event_type, winner, candidate_side, candidate_price,
                       candidate_seconds_left, mode_before, mode_after, reversal_streak,
                       loss_streak, details_json, market_id, official
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (*event_values, market_id),
            )

    def _process_ready_strategy_h_settlements(
        self, cfg: dict[str, float | bool]
    ) -> None:
        """Apply consecutive-market H transitions in original settlement order."""
        while True:
            settlement = self.db.execute(
                """SELECT * FROM market_settlements
                   WHERE h_processed=0
                   ORDER BY first_settled_at ASC, market_id ASC LIMIT 1"""
            ).fetchone()
            if settlement is None or settlement["status"] != "OFFICIAL":
                return
            market_id = int(settlement["market_id"])
            winner = str(settlement["official_winner"])
            h_trade = self.db.execute(
                """SELECT side FROM trades
                   WHERE market_id=? AND strategy='H' ORDER BY id ASC LIMIT 1""",
                (market_id,),
            ).fetchone()
            h_result = None
            if h_trade is not None:
                h_result = "WIN" if h_trade["side"] == winner else "LOSS"
            self._process_strategy_h_settlement(
                market_id=market_id,
                winner=winner,
                official=True,
                h_result=h_result,
                cfg=cfg,
            )
            self.db.execute(
                "UPDATE market_settlements SET h_processed=1 WHERE market_id=?",
                (market_id,),
            )

    @staticmethod
    def strategy_h_entry_candidate(
        snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Return one realistically fillable longshot candidate for an armed H state."""
        seconds_left = float(snapshot["seconds_left"])
        if not (0 < seconds_left <= float(cfg["strategy_h_entry_window_seconds"])):
            return None
        book_age = snapshot.get("book_age_ms")
        book_skew = snapshot.get("book_skew_ms")
        if (
            has_crossed_book(snapshot)
            or book_age is None
            or book_skew is None
            or float(book_age) > float(cfg["strategy_h_max_book_age_ms"])
            or float(book_skew) > float(cfg["strategy_h_max_book_skew_ms"])
        ):
            return None
        top_prices = [
            snapshot.get("up_ask"), snapshot.get("up_bid"),
            snapshot.get("down_ask"), snapshot.get("down_bid"),
        ]
        if any(value is None for value in top_prices):
            return None
        up_ask, up_bid, down_ask, down_bid = (float(value) for value in top_prices)
        if not (
            0 < up_ask <= 1
            and 0 <= up_bid <= up_ask
            and 0 < down_ask <= 1
            and 0 <= down_bid <= down_ask
        ):
            return None

        candidates: list[dict[str, Any]] = []
        for side in ("UP", "DOWN"):
            side_key = side.lower()
            ask = snapshot.get(f"{side_key}_ask")
            bid = snapshot.get(f"{side_key}_bid")
            visible_size = snapshot.get(f"{side_key}_ask_size")
            if ask is None or bid is None or visible_size is None:
                continue
            entry = float(ask)
            if not (0 < entry <= float(cfg["strategy_h_max_entry"])):
                continue
            fill = top_ask_fill(float(cfg["strategy_h_stake"]), entry, visible_size)
            if fill is None:
                continue
            candidates.append(
                {
                    "side": side,
                    "entry": entry,
                    "bid": float(bid),
                    "visible_size": float(visible_size),
                    "required_shares": fill["requested_shares"],
                    "book_age_ms": float(book_age),
                    "book_skew_ms": float(book_skew),
                    **fill,
                }
            )
        # Two simultaneous penny asks imply an invalid/broken binary book.
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def strategy_i_entry_candidate(
        snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Return one fillable penny longshot before the final safety cutoff."""
        if float(snapshot["seconds_left"]) <= float(cfg["strategy_i_min_seconds_left"]):
            return None
        book_age = snapshot.get("book_age_ms")
        book_skew = snapshot.get("book_skew_ms")
        if (
            has_crossed_book(snapshot)
            or book_age is None
            or book_skew is None
            or float(book_age) > float(cfg["strategy_i_max_book_age_ms"])
            or float(book_skew) > float(cfg["strategy_i_max_book_skew_ms"])
        ):
            return None

        top_prices = [
            snapshot.get("up_ask"), snapshot.get("up_bid"),
            snapshot.get("down_ask"), snapshot.get("down_bid"),
        ]
        if any(value is None for value in top_prices):
            return None
        up_ask, up_bid, down_ask, down_bid = (float(value) for value in top_prices)
        if not (
            0 < up_ask <= 1
            and 0 <= up_bid <= up_ask
            and 0 < down_ask <= 1
            and 0 <= down_bid <= down_ask
        ):
            return None

        qualifying_sides = [
            side
            for side in ("UP", "DOWN")
            if 0 < float(snapshot[f"{side.lower()}_ask"])
            <= float(cfg["strategy_i_max_entry"])
        ]
        if len(qualifying_sides) != 1:
            return None

        side = qualifying_sides[0]
        side_key = side.lower()
        entry = float(snapshot[f"{side_key}_ask"])
        visible_size = snapshot.get(f"{side_key}_ask_size")
        fill = top_ask_fill(float(cfg["strategy_i_stake"]), entry, visible_size)
        if fill is None:
            return None
        return {
            "side": side,
            "entry": entry,
            "bid": float(snapshot[f"{side_key}_bid"]),
            "visible_size": float(visible_size),
            "required_shares": fill["requested_shares"],
            "book_age_ms": float(book_age),
            "book_skew_ms": float(book_skew),
            **fill,
        }

    @staticmethod
    def strategy_j_entry_candidate(
        snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Return a fillable late-window mean-reversion target candidate."""
        seconds_left = float(snapshot["seconds_left"])
        if not (
            seconds_left > float(cfg["strategy_j_min_seconds_left"])
            and seconds_left <= float(cfg["strategy_j_window_seconds"])
        ):
            return None

        book_age = snapshot.get("book_age_ms")
        book_skew = snapshot.get("book_skew_ms")
        if book_age is None or book_skew is None or has_crossed_book(snapshot):
            return None
        book_age = float(book_age)
        book_skew = float(book_skew)
        if not (
            0 <= book_age <= float(cfg["strategy_j_max_book_age_ms"])
            and 0 <= book_skew <= float(cfg["strategy_j_max_book_skew_ms"])
        ):
            return None

        top_prices = [
            snapshot.get("up_ask"), snapshot.get("up_bid"),
            snapshot.get("down_ask"), snapshot.get("down_bid"),
        ]
        if any(value is None for value in top_prices):
            return None
        up_ask, up_bid, down_ask, down_bid = (float(value) for value in top_prices)
        if not (
            0 < up_ask <= 1
            and 0 <= up_bid <= up_ask
            and 0 < down_ask <= 1
            and 0 <= down_bid <= down_ask
        ):
            return None

        asks = {"UP": up_ask, "DOWN": down_ask}
        gap = abs(up_ask - down_ask)
        side, entry = min(asks.items(), key=lambda item: item[1])
        target = entry * float(cfg["strategy_j_target_multiplier"])
        if not (
            gap >= float(cfg["strategy_j_min_gap"])
            and entry <= float(cfg["strategy_j_max_entry"])
            and target <= 1
        ):
            return None

        visible_size = snapshot.get(f"{side.lower()}_ask_size")
        fill = top_ask_fill(float(cfg["strategy_j_stake"]), entry, visible_size)
        if fill is None:
            return None
        return {
            "side": side,
            "entry": entry,
            "bid": float(snapshot[f"{side.lower()}_bid"]),
            "target": target,
            "gap": gap,
            "visible_size": float(visible_size),
            "required_shares": fill["requested_shares"],
            "book_age_ms": book_age,
            "book_skew_ms": book_skew,
            **fill,
        }

    def open_trade(
        self,
        *,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        entry: float,
        target: float | None,
        stake: float,
        fee_rate_bps: int,
        note: str,
        strategy_version: str | None = None,
        model_probability: float | None = None,
        model_edge: float | None = None,
        model_sigma: float | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        shares = stake / entry
        fees = taker_fee(shares, entry, fee_rate_bps)
        with self.lock:
            self.db.execute(
                """INSERT INTO trades(
                    strategy, topic_id, market_id, side, status, entry_price, target_price,
                    stake, shares, fees, fee_rate_bps, opened_at, note, strategy_version,
                    model_probability, model_edge, model_sigma, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy, topic_id, market_id, side, "OPEN", entry, target, stake, shares,
                    fees, fee_rate_bps, utc_iso(), note, strategy_version,
                    model_probability, model_edge, model_sigma,
                    json.dumps(diagnostics, sort_keys=True) if diagnostics is not None else None,
                ),
            )
            self.db.commit()

    @staticmethod
    def _confirmation_add_initial_fills(
        base_price: float, fee_rate_bps: int
    ) -> list[dict[str, Any]]:
        levels = confirmation_add_levels(base_price)
        shares = CONFIRMATION_ADD_TRANCHE_USDT / float(base_price)
        return [
            {
                "index": index,
                "targetPrice": float(level),
                "triggered": index == 0,
                "stakeUsdt": CONFIRMATION_ADD_TRANCHE_USDT if index == 0 else 0.0,
                "shares": shares if index == 0 else 0.0,
                "fees": (
                    taker_fee(shares, float(base_price), fee_rate_bps)
                    if index == 0 else 0.0
                ),
                "events": [],
            }
            for index, level in enumerate(levels)
        ]

    def _create_confirmation_add_shadows_locked(
        self, snapshot: dict[str, Any]
    ) -> int:
        market_id = int(snapshot["market_id"])
        forward_started_at = str(self.db.execute(
            "SELECT forward_started_at FROM confirmation_add_shadow_meta WHERE singleton=1"
        ).fetchone()["forward_started_at"])
        placeholders = ",".join("?" for _ in CONFIRMATION_ADD_SOURCE_STRATEGIES)
        sources = self.db.execute(
            f"""SELECT t.* FROM trades AS t
                 LEFT JOIN confirmation_add_shadow_state AS c
                   ON c.source_trade_id=t.id
                WHERE t.market_id=? AND t.status='OPEN' AND t.opened_at>=?
                  AND t.strategy IN ({placeholders})
                  AND c.source_trade_id IS NULL
                ORDER BY t.id ASC""",
            (market_id, forward_started_at, *CONFIRMATION_ADD_SOURCE_STRATEGIES),
        ).fetchall()
        created = 0
        for source in sources:
            base_price = float(source["entry_price"])
            if not 0 < base_price < 1:
                continue
            fee_rate_bps = CONFIRMATION_ADD_FEE_BPS
            fills = self._confirmation_add_initial_fills(base_price, fee_rate_bps)
            stake = sum(float(item["stakeUsdt"]) for item in fills)
            shares = sum(float(item["shares"]) for item in fills)
            fees = sum(float(item["fees"]) for item in fills)
            levels = confirmation_add_levels(base_price)
            now = str(snapshot.get("timestamp") or utc_iso())
            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "forward_only": True,
                "source_strategy": str(source["strategy"]),
                "source_trade_id": int(source["id"]),
                "rule": "initial_1_usdt_then_add_1_usdt_at_+10pct_steps",
                "minimum_seconds_left_exclusive": 30.0,
                "maximum_total_stake_usdt": 5.0,
                "levels": list(levels),
                "fills": fills,
            }
            cursor = self.db.execute(
                """INSERT INTO trades(
                       strategy, topic_id, market_id, side, status, entry_price,
                       target_price, stake, shares, fees, fee_rate_bps, opened_at,
                       note, strategy_version, diagnostics_json
                   ) VALUES (?, ?, ?, ?, 'OPEN', ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    CONFIRMATION_ADD_STRATEGY,
                    int(source["topic_id"]),
                    market_id,
                    str(source["side"]),
                    base_price,
                    stake,
                    shares,
                    fees,
                    fee_rate_bps,
                    now,
                    (
                        f"forward-only confirmation-add Shadow from "
                        f"{source['strategy']} trade #{source['id']}; excluded from live routing"
                    ),
                    "confirmation_add_10_shadow_v1",
                    json.dumps(diagnostics, sort_keys=True),
                ),
            )
            shadow_trade_id = int(cursor.lastrowid)
            self.db.execute(
                """INSERT INTO confirmation_add_shadow_state(
                       source_trade_id, shadow_trade_id, source_strategy,
                       market_id, side, base_price, levels_json, fills_json,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)""",
                (
                    int(source["id"]),
                    shadow_trade_id,
                    str(source["strategy"]),
                    market_id,
                    str(source["side"]),
                    base_price,
                    json.dumps(list(levels)),
                    json.dumps(fills, sort_keys=True),
                    now,
                    now,
                ),
            )
            created += 1
        return created

    def process_confirmation_add_shadows(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Advance forward-only paper ladders from recorded, executable books."""
        market_id = int(snapshot["market_id"])
        with self.lock:
            created = self._create_confirmation_add_shadows_locked(snapshot)
            states = self.db.execute(
                """SELECT c.*, t.fee_rate_bps
                     FROM confirmation_add_shadow_state AS c
                     JOIN trades AS t ON t.id=c.shadow_trade_id
                    WHERE c.market_id=? AND c.status='ACTIVE'
                      AND t.status='OPEN'
                    ORDER BY c.source_trade_id ASC""",
                (market_id,),
            ).fetchall()
            filled_stake = 0.0
            filled_tranches: set[tuple[int, int]] = set()
            for side in ("UP", "DOWN"):
                side_states = [row for row in states if str(row["side"]) == side]
                if not side_states:
                    continue
                safe, _ = confirmation_add_book_is_safe(snapshot, side)
                if not safe:
                    continue
                event_key = confirmation_add_book_event_key(snapshot, side)
                actionable = [
                    row for row in side_states
                    if str(row["last_event_key"] or "") != event_key
                ]
                if not actionable:
                    continue
                prefix = side.lower()
                ask = float(snapshot[f"{prefix}_ask"])
                execution_price = confirmation_add_execution_price(ask)
                if execution_price is None:
                    continue
                available_shares = float(snapshot[f"{prefix}_ask_size"])
                for row in actionable:
                    levels = [float(value) for value in json.loads(row["levels_json"])]
                    fills = json.loads(row["fills_json"])
                    for index in range(1, len(fills)):
                        if ask + 1e-12 >= levels[index]:
                            fills[index]["triggered"] = True
                    for index in range(1, len(fills)):
                        item = fills[index]
                        if not item.get("triggered") or available_shares <= 1e-12:
                            continue
                        remaining_stake = max(
                            0.0,
                            CONFIRMATION_ADD_TRANCHE_USDT
                            - float(item.get("stakeUsdt") or 0.0),
                        )
                        if remaining_stake <= 1e-12:
                            continue
                        shares = min(
                            remaining_stake / execution_price,
                            available_shares,
                        )
                        if shares <= 1e-12:
                            continue
                        stake = shares * execution_price
                        fee = taker_fee(
                            shares,
                            execution_price,
                            int(row["fee_rate_bps"] or CONFIRMATION_ADD_FEE_BPS),
                        )
                        item["stakeUsdt"] = float(item.get("stakeUsdt") or 0.0) + stake
                        item["shares"] = float(item.get("shares") or 0.0) + shares
                        item["fees"] = float(item.get("fees") or 0.0) + fee
                        item.setdefault("events", []).append(
                            {
                                "eventKey": event_key,
                                "timestamp": snapshot.get("timestamp"),
                                "secondsLeft": float(snapshot["seconds_left"]),
                                "observedAsk": ask,
                                "executionPrice": execution_price,
                                "stakeUsdt": stake,
                                "shares": shares,
                                "fee": fee,
                                "bookAgeMs": float(snapshot["book_age_ms"]),
                                "bookSkewMs": float(snapshot["book_skew_ms"]),
                            }
                        )
                        available_shares -= shares
                        filled_stake += stake
                        if float(item["stakeUsdt"]) >= CONFIRMATION_ADD_TRANCHE_USDT - 1e-9:
                            filled_tranches.add((int(row["source_trade_id"]), index))
                    total_stake = sum(float(item.get("stakeUsdt") or 0.0) for item in fills)
                    total_shares = sum(float(item.get("shares") or 0.0) for item in fills)
                    total_fees = sum(float(item.get("fees") or 0.0) for item in fills)
                    average_entry = total_stake / total_shares if total_shares > 0 else float(row["base_price"])
                    diagnostics = {
                        "paper_only": True,
                        "live_orders_affected": False,
                        "forward_only": True,
                        "source_strategy": str(row["source_strategy"]),
                        "source_trade_id": int(row["source_trade_id"]),
                        "rule": "initial_1_usdt_then_add_1_usdt_at_+10pct_steps",
                        "minimum_seconds_left_exclusive": 30.0,
                        "maximum_total_stake_usdt": 5.0,
                        "levels": levels,
                        "fills": fills,
                    }
                    now = str(snapshot.get("timestamp") or utc_iso())
                    self.db.execute(
                        """UPDATE trades SET entry_price=?, stake=?, shares=?, fees=?,
                                  diagnostics_json=? WHERE id=?""",
                        (
                            average_entry,
                            total_stake,
                            total_shares,
                            total_fees,
                            json.dumps(diagnostics, sort_keys=True),
                            int(row["shadow_trade_id"]),
                        ),
                    )
                    self.db.execute(
                        """UPDATE confirmation_add_shadow_state
                              SET fills_json=?, last_event_key=?, updated_at=?
                            WHERE source_trade_id=?""",
                        (
                            json.dumps(fills, sort_keys=True),
                            event_key,
                            now,
                            int(row["source_trade_id"]),
                        ),
                    )
            self.db.commit()
        return {
            "created": created,
            "filledStakeUsdt": filled_stake,
            "completedTranches": len(filled_tranches),
            "paperOnly": True,
            "liveOrdersAffected": False,
        }

    def confirmation_add_shadow_summary(self, recent_limit: int = 50) -> dict[str, Any]:
        with self.lock:
            rows = [dict(row) for row in self.db.execute(
                """SELECT c.*, t.status AS trade_status, t.stake, t.shares,
                          t.fees, t.pnl, t.opened_at, t.closed_at
                     FROM confirmation_add_shadow_state AS c
                     JOIN trades AS t ON t.id=c.shadow_trade_id
                    WHERE c.status!='EXCLUDED_PREDEPLOY'
                    ORDER BY c.source_trade_id DESC"""
            ).fetchall()]

        def cohort(items: list[dict[str, Any]]) -> dict[str, Any]:
            official = [item for item in items if item["status"] == "OFFICIAL"]
            stake = sum(float(item["stake"] or 0.0) for item in official)
            fees = sum(float(item["fees"] or 0.0) for item in official)
            pnl = sum(float(item["pnl"] or 0.0) for item in official)
            wins = sum(item["trade_status"] == "SETTLED_WIN" for item in official)
            return {
                "samples": len(items),
                "officialSamples": len(official),
                "pendingSamples": len(items) - len(official),
                "wins": int(wins),
                "losses": len(official) - int(wins),
                "stakeUsdt": stake,
                "feesUsdt": fees,
                "pnlUsdt": pnl,
                "returnOnCostPct": pnl / (stake + fees) * 100.0 if stake + fees > 0 else None,
            }

        by_source = {
            source: cohort([row for row in rows if row["source_strategy"] == source])
            for source in CONFIRMATION_ADD_SOURCE_STRATEGIES
        }
        recent = []
        for row in rows[:max(1, min(200, int(recent_limit)))]:
            fills = json.loads(str(row["fills_json"] or "[]"))
            recent.append(
                {
                    "sourceTradeId": int(row["source_trade_id"]),
                    "shadowTradeId": int(row["shadow_trade_id"]),
                    "sourceStrategy": row["source_strategy"],
                    "marketId": int(row["market_id"]),
                    "side": row["side"],
                    "basePrice": row["base_price"],
                    "filledTranches": sum(
                        float(item.get("stakeUsdt") or 0.0) >= 1.0 - 1e-9
                        for item in fills
                    ),
                    "stakeUsdt": row["stake"],
                    "feesUsdt": row["fees"],
                    "status": row["status"],
                    "result": row["trade_status"],
                    "pnlUsdt": row["pnl"],
                    "openedAt": row["opened_at"],
                    "closedAt": row["closed_at"],
                    "fills": fills,
                }
            )
        return {
            "strategy": CONFIRMATION_ADD_STRATEGY,
            "status": "FORWARD_ONLY",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "sourceStrategies": list(CONFIRMATION_ADD_SOURCE_STRATEGIES),
            "rule": {
                "initialStakeUsdt": 1.0,
                "addStakeUsdt": 1.0,
                "multipliers": [1.0, 1.1, 1.2, 1.3, 1.4],
                "minimumSecondsLeftExclusive": 30.0,
                "maximumStakeUsdt": 5.0,
                "slippageBps": 50.0,
                "feeBps": 200,
                "maxSpread": 0.03,
                "maxBookAgeMs": 2000.0,
                "maxBookSkewMs": 500.0,
            },
            "overall": cohort(rows),
            "bySource": by_source,
            "recent": recent,
        }

    @staticmethod
    def strategy_m_entry_candidate(
        snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Build M's opening direction exclusively from spot versus official strike."""
        try:
            seconds_left = float(snapshot["seconds_left"])
            start_price = float(snapshot["start_price"])
            spot_price = float(snapshot["spot_price"])
        except (KeyError, TypeError, ValueError):
            return None
        # M is intentionally scoped to this collector's fixed five-minute market.
        elapsed = 300.0 - seconds_left
        if not (
            math.isfinite(seconds_left)
            and 0 <= elapsed <= float(cfg["strategy_m_entry_window_seconds"])
            and math.isfinite(start_price)
            and start_price > 0
            and math.isfinite(spot_price)
            and spot_price > 0
            and spot_price != start_price
        ):
            return None

        side = "UP" if spot_price > start_price else "DOWN"
        side_key = side.lower()
        try:
            top = {
                key: float(snapshot[key])
                for key in ("up_ask", "up_bid", "down_ask", "down_bid")
            }
            visible_size = float(snapshot[f"{side_key}_ask_size"])
            book_skew = float(snapshot["book_skew_ms"])
            book_age = float(snapshot["book_age_ms"])
        except (KeyError, TypeError, ValueError):
            return None
        entry = top[f"{side_key}_ask"]
        bid = top[f"{side_key}_bid"]
        if not (
            all(math.isfinite(value) for value in top.values())
            and 0 < top["up_ask"] <= 1
            and 0 <= top["up_bid"] <= top["up_ask"]
            and 0 < top["down_ask"] <= 1
            and 0 <= top["down_bid"] <= top["down_ask"]
            and math.isfinite(book_skew)
            and 0 <= book_skew <= float(cfg["strategy_m_max_book_skew_ms"])
            and math.isfinite(book_age)
            and 0 <= book_age <= float(cfg["strategy_m_max_book_age_ms"])
        ):
            return None
        fill = top_ask_fill(float(cfg["strategy_m_stake"]), entry, visible_size)
        if fill is None:
            return None
        delta = spot_price - start_price
        return {
            "side": side,
            "entry": entry,
            "bid": bid,
            "seconds_left": seconds_left,
            "elapsed": elapsed,
            "start_price": start_price,
            "spot_price": spot_price,
            "signal_delta": delta,
            "signal_delta_bps": delta / start_price * 10_000.0,
            "visible_size": visible_size,
            "book_skew_ms": book_skew,
            "book_age_ms": book_age,
            **fill,
        }

    @staticmethod
    def _m_signal_context(
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        *,
        prefix: str,
        price_field: str = "spot_price",
        require_nonzero: bool = True,
        min_abs_delta_bps: float = 0.0,
    ) -> dict[str, Any] | None:
        try:
            seconds_left = float(snapshot["seconds_left"])
            start_price = float(snapshot["start_price"])
            signal_price = float(snapshot[price_field])
        except (KeyError, TypeError, ValueError):
            return None
        elapsed = 300.0 - seconds_left
        if not (
            math.isfinite(seconds_left)
            and 0 <= elapsed <= float(cfg[f"strategy_{prefix}_entry_window_seconds"])
            and math.isfinite(start_price)
            and start_price > 0
            and math.isfinite(signal_price)
            and signal_price > 0
        ):
            return None
        delta = signal_price - start_price
        delta_bps = delta / start_price * 10_000.0
        if require_nonzero and delta == 0:
            return None
        if abs(delta_bps) < float(min_abs_delta_bps):
            return None
        return {
            "seconds_left": seconds_left,
            "elapsed": elapsed,
            "start_price": start_price,
            "signal_price": signal_price,
            "signal_price_field": price_field,
            "signal_delta": delta,
            "signal_delta_bps": delta_bps,
            "side": "UP" if delta > 0 else ("DOWN" if delta < 0 else None),
        }

    @staticmethod
    def _m_execution_context(
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        *,
        prefix: str,
        side: str,
    ) -> dict[str, Any] | None:
        try:
            top = {
                key: float(snapshot[key])
                for key in ("up_ask", "up_bid", "down_ask", "down_bid")
            }
            visible_size = float(snapshot[f"{side.lower()}_ask_size"])
            book_skew = float(snapshot["book_skew_ms"])
            book_age = float(snapshot["book_age_ms"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (
            all(math.isfinite(value) for value in top.values())
            and 0 < top["up_ask"] <= 1
            and 0 <= top["up_bid"] <= top["up_ask"]
            and 0 < top["down_ask"] <= 1
            and 0 <= top["down_bid"] <= top["down_ask"]
            and math.isfinite(book_skew)
            and 0 <= book_skew <= float(cfg[f"strategy_{prefix}_max_book_skew_ms"])
            and math.isfinite(book_age)
            and 0 <= book_age <= float(cfg[f"strategy_{prefix}_max_book_age_ms"])
        ):
            return None
        entry = top[f"{side.lower()}_ask"]
        fill = top_ask_fill(float(cfg[f"strategy_{prefix}_stake"]), entry, visible_size)
        if fill is None:
            return None
        return {
            "side": side,
            "entry": entry,
            "bid": top[f"{side.lower()}_bid"],
            "visible_size": visible_size,
            "book_skew_ms": book_skew,
            "book_age_ms": book_age,
            **fill,
        }

    @staticmethod
    def _m_event_key(
        snapshot: dict[str, Any], realtime_context: dict[str, Any]
    ) -> str:
        sequence = realtime_context.get(
            "signal_event_sequence", snapshot.get("signal_event_sequence")
        )
        if sequence is not None:
            return f"sequence:{sequence}"
        return json.dumps(
            [
                snapshot.get("timestamp"),
                snapshot.get("seconds_left"),
                snapshot.get("spot_price"),
                snapshot.get("futures_price"),
                snapshot.get("up_ask"),
                snapshot.get("down_ask"),
            ],
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _m_event_type(realtime_context: dict[str, Any] | None) -> str:
        """Normalize the realtime engine's event label without coupling to it."""
        if realtime_context is None:
            return "rest"
        explicit = str(realtime_context.get("signal_event_type") or "").lower()
        if explicit in {"spot", "futures", "prediction", "scheduler"}:
            return explicit
        source = str(realtime_context.get("trigger_source") or "").lower()
        if "prediction" in source or "orderbook" in source or "book" in source:
            return "prediction"
        if "future" in source or "perpetual" in source or "usdm" in source:
            return "futures"
        if "spot" in source:
            return "spot"
        if "scheduler" in source or "timer" in source:
            return "scheduler"
        return "unknown"

    def _store_m_signal(
        self,
        strategy: str,
        snapshot: dict[str, Any],
        signal: dict[str, Any] | None,
        realtime_context: dict[str, Any],
    ) -> sqlite3.Row | None:
        """Freeze the first valid signal so a later book event cannot rewrite it."""
        market_id = int(snapshot["market_id"])
        cache_key = (strategy, market_id)
        if cache_key not in self._m_signal_row_cache:
            self._m_signal_row_cache[cache_key] = self.db.execute(
                "SELECT * FROM strategy_m_signals WHERE strategy=? AND market_id=?",
                cache_key,
            ).fetchone()
        existing = self._m_signal_row_cache[cache_key]
        if existing is not None or signal is None or signal.get("side") not in {"UP", "DOWN"}:
            return existing
        received_monotonic_ns = realtime_context.get(
            "received_monotonic_ns", snapshot.get("received_monotonic_ns")
        )
        if (
            self._m_event_type(realtime_context) != "rest"
            and received_monotonic_ns is None
        ):
            return existing
        exchange_event_ms = realtime_context.get(
            "signal_exchange_event_ms",
            realtime_context.get("exchange_event_ms", snapshot.get("exchange_event_ms")),
        )
        signal_data = {
            **signal,
            "signal_timestamp": snapshot.get("timestamp"),
            "signal_exchange_event_ms": exchange_event_ms,
        }
        self.db.execute(
            """INSERT OR IGNORE INTO strategy_m_signals(
                   strategy, market_id, side, signal_timestamp, signal_elapsed,
                   signal_received_monotonic_ns, signal_exchange_event_ms,
                   signal_json, realtime_context_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy,
                market_id,
                signal["side"],
                snapshot.get("timestamp"),
                float(signal["elapsed"]),
                int(received_monotonic_ns) if received_monotonic_ns is not None else None,
                float(exchange_event_ms) if exchange_event_ms is not None else None,
                json.dumps(signal_data, sort_keys=True, default=str),
                json.dumps(realtime_context, sort_keys=True, default=str),
                utc_iso(),
            ),
        )
        row = self.db.execute(
            "SELECT * FROM strategy_m_signals WHERE strategy=? AND market_id=?",
            (strategy, market_id),
        ).fetchone()
        self._m_signal_row_cache[cache_key] = row
        return row

    def _get_m_signal(self, strategy: str, market_id: int) -> sqlite3.Row | None:
        cache_key = (strategy, market_id)
        if cache_key not in self._m_signal_row_cache:
            self._m_signal_row_cache[cache_key] = self.db.execute(
                "SELECT * FROM strategy_m_signals WHERE strategy=? AND market_id=?",
                cache_key,
            ).fetchone()
        return self._m_signal_row_cache[cache_key]

    @staticmethod
    def _m_signal_row_data(row: sqlite3.Row | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if row is None:
            return None, {}
        signal = json.loads(row["signal_json"])
        context = (
            json.loads(row["realtime_context_json"])
            if row["realtime_context_json"]
            else {}
        )
        return signal, context

    @staticmethod
    def _m_book_is_after_signal(
        signal_received_monotonic_ns: Any,
        snapshot: dict[str, Any],
        realtime_context: dict[str, Any],
    ) -> bool:
        """Reject a cached Prediction book received before the frozen signal."""
        event_type = Store._m_event_type(realtime_context)
        if signal_received_monotonic_ns is None:
            return event_type == "rest"
        book_ns = realtime_context.get(
            "prediction_book_received_monotonic_ns",
            snapshot.get("prediction_book_received_monotonic_ns"),
        )
        if book_ns is None and Store._m_event_type(realtime_context) == "prediction":
            book_ns = realtime_context.get(
                "received_monotonic_ns", snapshot.get("received_monotonic_ns")
            )
        if book_ns is None:
            # REST fallback has no cross-stream receive timestamp to compare.
            return event_type == "rest"
        try:
            return int(book_ns) > int(signal_received_monotonic_ns)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _m_realtime_diagnostics(
        snapshot: dict[str, Any],
        realtime_context: dict[str, Any] | None,
        *,
        decision_started_wall_ns: int,
        decision_started_monotonic_ns: int,
        signal_exchange_event_ms: Any = None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for key in (
            "exchange_event_ms",
            "prediction_book_version_ms",
            "signal_prediction_book_version_ms",
            "received_wall_ns",
            "received_monotonic_ns",
            "signal_event_sequence",
            "prediction_book_received_wall_ns",
            "prediction_book_received_monotonic_ns",
            "prediction_book_age_ms",
            "trigger_source",
        ):
            if snapshot.get(key) is not None:
                merged[key] = snapshot[key]
        if realtime_context:
            merged.update(realtime_context)
        submitted_wall_ns = time.time_ns()
        submitted_monotonic_ns = time.monotonic_ns()
        received_monotonic_ns = merged.get("received_monotonic_ns")
        received_wall_ns = merged.get("received_wall_ns")

        def elapsed_ms(later: int, earlier: Any) -> float | None:
            try:
                value = (later - int(earlier)) / 1_000_000.0
            except (TypeError, ValueError):
                return None
            return value if value >= 0 else None

        queue_delay_ms = merged.get("queue_delay_ms")
        if queue_delay_ms is None:
            queue_delay_ms = elapsed_ms(
                decision_started_monotonic_ns, received_monotonic_ns
            )
        receive_to_submit_ms = elapsed_ms(
            submitted_monotonic_ns, received_monotonic_ns
        )
        if receive_to_submit_ms is None:
            receive_to_submit_ms = elapsed_ms(submitted_wall_ns, received_wall_ns)
        result = dict(merged)
        result.update(
            {
                "signal_exchange_event_ms": (
                    signal_exchange_event_ms
                    if signal_exchange_event_ms is not None
                    else merged.get("signal_exchange_event_ms", merged.get("exchange_event_ms"))
                ),
                "received_wall_ns": received_wall_ns,
                "received_monotonic_ns": received_monotonic_ns,
                "decision_started_wall_ns": decision_started_wall_ns,
                "decision_started_monotonic_ns": decision_started_monotonic_ns,
                "queue_delay_ms": queue_delay_ms,
                "prediction_book_received_wall_ns": merged.get(
                    "prediction_book_received_wall_ns"
                ),
                "prediction_book_received_monotonic_ns": merged.get(
                    "prediction_book_received_monotonic_ns"
                ),
                "prediction_book_age_ms": merged.get(
                    "prediction_book_age_ms", snapshot.get("book_age_ms")
                ),
                "trigger_source": merged.get("trigger_source", "rest_poll"),
                "signal_event_sequence": merged.get(
                    "signal_event_sequence", snapshot.get("signal_event_sequence")
                ),
                "simulated_order_submitted_wall_ns": submitted_wall_ns,
                "simulated_order_submitted_monotonic_ns": submitted_monotonic_ns,
                "receive_to_submit_ms": receive_to_submit_ms,
                "decision_to_submit_ms": elapsed_ms(
                    submitted_monotonic_ns, decision_started_monotonic_ns
                ),
            }
        )
        return json.loads(json.dumps(result, default=str))

    def _open_m_series_trade(
        self,
        *,
        strategy: str,
        version: str,
        snapshot: dict[str, Any],
        fee_bps: int,
        cfg: dict[str, float | bool],
        config_prefix: str,
        candidate: dict[str, Any],
        signal_source: str,
        direction_rule: str,
        realtime_context: dict[str, Any] | None,
        decision_started_wall_ns: int,
        decision_started_monotonic_ns: int,
        extra_diagnostics: dict[str, Any] | None = None,
    ) -> bool:
        market_id = int(snapshot["market_id"])
        if self.has_trade(strategy, market_id):
            return False
        signal_exchange_event_ms = candidate.get("signal_exchange_event_ms")
        diagnostics = {
            "execution_model": "top_ask_partial_fill_v1",
            "signal_source": signal_source,
            "direction_rule": direction_rule,
            "direction_uses_prediction_price": False,
            "prediction_market_role": "execution_only",
            "signal_timestamp": candidate.get(
                "signal_timestamp", snapshot.get("timestamp")
            ),
            "execution_timestamp": snapshot.get("timestamp"),
            "seconds_left": float(snapshot["seconds_left"]),
            "elapsed_seconds": 300.0 - float(snapshot["seconds_left"]),
            "signal_side": candidate["side"],
            "official_start_price": candidate.get(
                "start_price", snapshot.get("start_price")
            ),
            "official_strike": candidate.get(
                "start_price", snapshot.get("start_price")
            ),
            "spot_price": snapshot.get("spot_price"),
            "current_spot": snapshot.get("spot_price"),
            "signal_price": candidate.get("signal_price"),
            "signal_price_field": candidate.get("signal_price_field"),
            "signal_delta": candidate.get("signal_delta"),
            "signal_delta_bps": candidate.get("signal_delta_bps"),
            "distance": candidate.get("signal_delta"),
            "distance_bps": candidate.get("signal_delta_bps"),
            "quoted_ask": candidate["entry"],
            "quoted_bid": candidate["bid"],
            "visible_ask_size": candidate["visible_size"],
            "requested_stake": candidate["requested_stake"],
            "requested_shares": candidate["requested_shares"],
            "filled_stake": candidate["filled_stake"],
            "filled_shares": candidate["filled_shares"],
            "fill_ratio": candidate["fill_ratio"],
            "partial_fill": candidate["partial_fill"],
            "book_skew_ms": candidate["book_skew_ms"],
            "book_age_ms": candidate["book_age_ms"],
            "realtime_context": self._m_realtime_diagnostics(
                snapshot,
                realtime_context,
                decision_started_wall_ns=decision_started_wall_ns,
                decision_started_monotonic_ns=decision_started_monotonic_ns,
                signal_exchange_event_ms=signal_exchange_event_ms,
            ),
            "config": strategy_config_snapshot(cfg, config_prefix),
        }
        if extra_diagnostics:
            diagnostics.update(extra_diagnostics)
        self.open_trade(
            strategy=strategy,
            topic_id=int(snapshot["topic_id"]),
            market_id=market_id,
            side=str(candidate["side"]),
            entry=float(candidate["entry"]),
            target=None,
            stake=float(candidate["filled_stake"]),
            fee_rate_bps=fee_bps,
            strategy_version=version,
            diagnostics=diagnostics,
            note=(
                f"{strategy} {direction_rule}; {candidate['side']} "
                f"delta={float(candidate.get('signal_delta_bps') or 0):+.4f}bps; "
                f"{'partial' if candidate['partial_fill'] else 'full'} fill "
                f"{float(candidate['filled_shares']):.4f}/"
                f"{float(candidate['requested_shares']):.4f} shares"
            ),
        )
        return True

    def _strategy_m01r_rebound_candidate(
        self,
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        candidate: dict[str, Any] | None,
        realtime_context: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Arm below the configured price and buy the first +rebound crossing."""
        if candidate is None:
            return None, {}
        market_id = int(snapshot["market_id"])
        side = str(candidate["side"])
        ask = float(candidate["entry"])
        max_anchor = float(cfg["strategy_m01r_max_anchor"])
        rebound = float(cfg["strategy_m01r_rebound"])
        observed_at = str(snapshot.get("timestamp") or utc_iso())
        event_sequence = realtime_context.get("signal_event_sequence")
        state = self.db.execute(
            "SELECT * FROM strategy_m01r_state WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if state is None:
            if ask <= max_anchor + 1e-12:
                trigger_price = ask + rebound
                self.db.execute(
                    """INSERT INTO strategy_m01r_state(
                           market_id, side, low_ask, trigger_price, armed_at,
                           low_observed_at, low_event_sequence, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        market_id,
                        side,
                        ask,
                        trigger_price,
                        observed_at,
                        observed_at,
                        str(event_sequence) if event_sequence is not None else None,
                        utc_iso(),
                    ),
                )
            return None, {}
        if str(state["side"]) != side:
            return None, {}

        low_ask = float(state["low_ask"])
        trigger_price = float(state["trigger_price"])
        if ask < low_ask - 1e-12 and ask <= max_anchor + 1e-12:
            low_ask = ask
            trigger_price = ask + rebound
            self.db.execute(
                """UPDATE strategy_m01r_state
                   SET low_ask=?, trigger_price=?, low_observed_at=?,
                       low_event_sequence=?, updated_at=?
                   WHERE market_id=?""",
                (
                    low_ask,
                    trigger_price,
                    observed_at,
                    str(event_sequence) if event_sequence is not None else None,
                    utc_iso(),
                    market_id,
                ),
            )
            return None, {}

        observed_rebound = ask - low_ask
        if ask + 1e-12 < trigger_price:
            return None, {}
        return candidate, {
            "maximum_anchor_price": max_anchor,
            "configured_rebound_amount": rebound,
            "rebound_anchor_price": low_ask,
            "rebound_trigger_price": trigger_price,
            "observed_rebound_amount": observed_rebound,
            "rebound_armed_at": state["armed_at"],
            "rebound_low_observed_at": state["low_observed_at"],
            "rebound_low_event_sequence": state["low_event_sequence"],
        }

    def _strategy_m4_candidate(
        self,
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        realtime_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        market_id = int(snapshot["market_id"])
        event_key = self._m_event_key(snapshot, realtime_context)
        prior = self.db.execute(
            "SELECT * FROM strategy_m4_state WHERE market_id=?", (market_id,)
        ).fetchone()
        if prior is not None and str(prior["last_event_key"]) == event_key:
            return None
        signal = self._m_signal_context(snapshot, cfg, prefix="m4")
        required = int(cfg["strategy_m4_required_observations"])
        if signal is None:
            self.db.execute(
                """INSERT INTO strategy_m4_state(
                       market_id, last_event_key, last_side,
                       consecutive_observations, observations_json, updated_at
                   ) VALUES (?, ?, NULL, 0, '[]', ?)
                   ON CONFLICT(market_id) DO UPDATE SET
                       last_event_key=excluded.last_event_key,
                       last_side=NULL, consecutive_observations=0,
                       observations_json='[]', updated_at=excluded.updated_at""",
                (market_id, event_key, utc_iso()),
            )
            return None
        observation = {
            "event_key": event_key,
            "timestamp": snapshot.get("timestamp"),
            "side": signal["side"],
            "spot_price": signal["signal_price"],
            "official_start_price": signal["start_price"],
            "distance_bps": signal["signal_delta_bps"],
            "signal_event_sequence": realtime_context.get("signal_event_sequence"),
        }
        previous_observations = (
            json.loads(prior["observations_json"])
            if prior is not None and prior["observations_json"]
            else []
        )
        same_side = prior is not None and str(prior["last_side"]) == signal["side"]
        observations = (previous_observations + [observation]) if same_side else [observation]
        observations = observations[-required:]
        count = int(prior["consecutive_observations"]) + 1 if same_side else 1
        self.db.execute(
            """INSERT INTO strategy_m4_state(
                   market_id, last_event_key, last_side,
                   consecutive_observations, observations_json, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(market_id) DO UPDATE SET
                   last_event_key=excluded.last_event_key,
                   last_side=excluded.last_side,
                   consecutive_observations=excluded.consecutive_observations,
                   observations_json=excluded.observations_json,
                   updated_at=excluded.updated_at""",
            (
                market_id,
                event_key,
                signal["side"],
                count,
                json.dumps(observations, sort_keys=True),
                utc_iso(),
            ),
        )
        if count < required:
            return None
        return {
            **signal,
            "confirmation_observations": observations,
            "confirmation_count": count,
        }

    def _record_m_basis_sample(
        self,
        snapshot: dict[str, Any],
        realtime_context: dict[str, Any],
    ) -> sqlite3.Row | None:
        """Persist each market's canonical first non-zero WS Spot deviation.

        The fixed capture window deliberately does not read an M6 strategy
        setting.  Changing a cohort's enabled flag or entry configuration must
        never change the historical estimator dataset.
        """
        market_id = int(snapshot["market_id"])
        existing = self.db.execute(
            "SELECT * FROM strategy_m_basis_samples WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if existing is not None:
            return existing
        try:
            seconds_left = float(snapshot["seconds_left"])
            start_price = float(snapshot["start_price"])
            spot_price = float(snapshot["spot_price"])
        except (KeyError, TypeError, ValueError):
            return None
        elapsed = 300.0 - seconds_left
        if not (
            math.isfinite(elapsed)
            and 0 <= elapsed <= M6_BASIS_CAPTURE_WINDOW_SECONDS
            and math.isfinite(start_price)
            and start_price > 0
            and math.isfinite(spot_price)
            and spot_price > 0
        ):
            return None
        basis_bps = (spot_price / start_price - 1.0) * 10_000.0
        if not math.isfinite(basis_bps) or basis_bps == 0:
            return None
        received_monotonic_ns = realtime_context.get(
            "received_monotonic_ns", snapshot.get("received_monotonic_ns")
        )
        if received_monotonic_ns is None:
            return None
        event_sequence = realtime_context.get(
            "signal_event_sequence", snapshot.get("signal_event_sequence")
        )
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO strategy_m_basis_samples(
                   market_id, captured_at, start_price, spot_price, basis_bps,
                   signal_received_monotonic_ns, signal_event_sequence, source
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                snapshot.get("timestamp") or utc_iso(),
                start_price,
                spot_price,
                basis_bps,
                int(received_monotonic_ns),
                str(event_sequence) if event_sequence is not None else None,
                "binance_spot_ws_first_nonzero_vs_prediction_start",
            ),
        )
        if cursor.rowcount:
            self._m_basis_sample_generation += 1
            self._m_basis_cache.clear()
        return self.db.execute(
            "SELECT * FROM strategy_m_basis_samples WHERE market_id=?",
            (market_id,),
        ).fetchone()

    def _strategy_m6_basis_context(
        self, market_id: int, cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        minimum = int(cfg["strategy_m6_min_basis_samples"])
        cache_key = (market_id, minimum, self._m_basis_sample_generation)
        if cache_key in self._m_basis_cache:
            return self._m_basis_cache[cache_key]
        current = self.db.execute(
            "SELECT id FROM strategy_m_basis_samples WHERE market_id=?",
            (market_id,),
        ).fetchone()
        if current is None:
            return None
        cutoff_id = int(current["id"])
        rows = self.db.execute(
            """SELECT id, market_id, basis_bps
               FROM strategy_m_basis_samples
               WHERE id < ?
               ORDER BY id ASC""",
            (cutoff_id,),
        ).fetchall()
        samples = [
            (int(row["id"]), int(row["market_id"]), float(row["basis_bps"]))
            for row in rows
            if math.isfinite(float(row["basis_bps"]))
        ]
        if len(samples) < minimum:
            self._m_basis_cache[cache_key] = None
            return None
        result = {
            "basis_sample_count": len(samples),
            "basis_mean_bps": sum(value for _, _, value in samples) / len(samples),
            "basis_first_market_id": samples[0][1],
            "basis_last_market_id": samples[-1][1],
            "basis_first_sample_id": samples[0][0],
            "basis_last_sample_id": samples[-1][0],
            "basis_causal_cutoff_sample_id": cutoff_id,
            "basis_sample_scope": "previous_market_ws_first_nonzero_spot_signal_only",
            "basis_source_table": "strategy_m_basis_samples",
            "no_lookahead": True,
        }
        self._m_basis_cache[cache_key] = result
        return result

    def _strategy_m7_signal(
        self,
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        realtime_context: dict[str, Any],
    ) -> sqlite3.Row | None:
        market_id = int(snapshot["market_id"])
        if market_id not in self._m7_signal_row_cache:
            self._m7_signal_row_cache[market_id] = self.db.execute(
                "SELECT * FROM strategy_m7_signals WHERE market_id=?", (market_id,)
            ).fetchone()
        existing = self._m7_signal_row_cache[market_id]
        if existing is not None:
            return existing
        signal = self._m_signal_context(snapshot, cfg, prefix="m7")
        if signal is None:
            return None
        signal_data = {
            **signal,
            "signal_timestamp": snapshot.get("timestamp"),
            "signal_exchange_event_ms": realtime_context.get(
                "signal_exchange_event_ms",
                realtime_context.get(
                    "exchange_event_ms", snapshot.get("exchange_event_ms")
                ),
            ),
        }
        received_monotonic_ns = realtime_context.get(
            "received_monotonic_ns", snapshot.get("received_monotonic_ns")
        )
        self.db.execute(
            """INSERT OR IGNORE INTO strategy_m7_signals(
                   market_id, side, signal_timestamp, signal_elapsed,
                   signal_received_monotonic_ns, signal_json,
                   realtime_context_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                signal["side"],
                snapshot.get("timestamp"),
                signal["elapsed"],
                int(received_monotonic_ns) if received_monotonic_ns is not None else None,
                json.dumps(signal_data, sort_keys=True),
                json.dumps(realtime_context, sort_keys=True, default=str),
                utc_iso(),
            ),
        )
        row = self.db.execute(
            "SELECT * FROM strategy_m7_signals WHERE market_id=?", (market_id,)
        ).fetchone()
        self._m7_signal_row_cache[market_id] = row
        return row

    def _get_m7_signal(self, market_id: int) -> sqlite3.Row | None:
        if market_id not in self._m7_signal_row_cache:
            self._m7_signal_row_cache[market_id] = self.db.execute(
                "SELECT * FROM strategy_m7_signals WHERE market_id=?", (market_id,)
            ).fetchone()
        return self._m7_signal_row_cache[market_id]

    @staticmethod
    def _mx_event_sequence(
        snapshot: dict[str, Any], context: dict[str, Any]
    ) -> str:
        sequence = context.get(
            "signal_event_sequence", snapshot.get("signal_event_sequence")
        )
        if sequence is not None:
            return str(sequence)
        return Store._m_event_key(snapshot, context)

    @staticmethod
    def _mx_order_status(requested: float, filled: float) -> str:
        if filled >= requested - 1e-12:
            return "FILLED"
        return "PARTIAL" if filled > 1e-12 else "OPEN"

    @staticmethod
    def _mx_variant(strategy: str) -> str:
        if strategy.startswith("M0X_"):
            return strategy[4:]
        if strategy.startswith("MX_"):
            return strategy[3:]
        return strategy

    def _mx_create_intents(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        cfg: dict[str, float | bool],
    ) -> None:
        if self._m_event_type(context) != "spot":
            return
        market_id = int(snapshot["market_id"])
        received_ns = context.get(
            "received_monotonic_ns", snapshot.get("received_monotonic_ns")
        )
        if received_ns is None:
            return
        try:
            topic_id = int(snapshot["topic_id"])
            elapsed = 300.0 - float(snapshot["seconds_left"])
            signal_price = float(snapshot["spot_price"])
            start_price = float(snapshot["start_price"])
        except (KeyError, TypeError, ValueError):
            return
        requested_shares = float(cfg["strategy_m_stake"]) / MX_ENTRY_LIMIT
        deadline = float(cfg["strategy_m_entry_window_seconds"])
        if elapsed > deadline:
            return
        event_sequence = self._mx_event_sequence(snapshot, context)
        m_signal = self._m_signal_context(snapshot, cfg, prefix="m")
        seed = int(cfg["strategy_m0_seed"])
        digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
        m0_side = "UP" if digest[0] < 128 else "DOWN"
        groups = (
            (
                "M",
                MX_STRATEGIES,
                m_signal,
                "first_nonzero_spot_minus_official_start",
                "strategy_mx_enabled",
            ),
            (
                "M0",
                M0X_STRATEGIES,
                {
                    "side": m0_side,
                    "signal_price": signal_price,
                    "start_price": start_price,
                },
                "seeded_random_sha256_market_id",
                "strategy_m0x_enabled",
            ),
        )
        for family, family_strategies, signal, direction_rule, master_key in groups:
            if not bool(cfg[master_key]):
                continue
            family_strategies = tuple(
                strategy
                for strategy in family_strategies
                if bool(cfg.get(f"strategy_{strategy.lower()}_enabled", True))
            )
            if not family_strategies:
                continue
            intent_key = (family, market_id)
            if intent_key in self._mx_intent_markets:
                continue
            placeholders = ",".join("?" for _ in family_strategies)
            if self.db.execute(
                f"""SELECT 1 FROM strategy_mx_positions
                    WHERE market_id=? AND strategy IN ({placeholders}) LIMIT 1""",
                (market_id, *family_strategies),
            ).fetchone() is not None:
                self._mx_intent_markets.add(intent_key)
                continue
            if signal is None or signal.get("side") not in {"UP", "DOWN"}:
                continue
            created_at = utc_iso()
            diagnostics = json.dumps(
                {
                    "version": MX_LEDGER_VERSION,
                    "signalFamily": family,
                    "entryLimit": MX_ENTRY_LIMIT,
                    "entryWindowSeconds": deadline,
                    "requestedStakeAtLimit": float(cfg["strategy_m_stake"]),
                    "directionRule": direction_rule,
                    "randomSeed": seed if family == "M0" else None,
                    "signalRealtimeContext": context,
                },
                sort_keys=True,
                default=str,
            )
            for strategy in family_strategies:
                cursor = self.db.execute(
                    """INSERT OR IGNORE INTO strategy_mx_positions(
                           strategy, market_id, topic_id, side, status,
                           signal_timestamp, signal_received_monotonic_ns,
                           signal_event_sequence, signal_spot_price, start_price,
                           entry_deadline_elapsed, requested_entry_shares,
                           rev_spot_armed, diagnostics_json, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, 'ENTRY_OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        strategy,
                        market_id,
                        topic_id,
                        str(signal["side"]),
                        snapshot.get("timestamp"),
                        int(received_ns),
                        event_sequence,
                        float(signal["signal_price"]),
                        float(signal["start_price"]),
                        deadline,
                        requested_shares,
                        int(
                            (signal["side"] == "UP" and signal["signal_price"] > signal["start_price"])
                            or (signal["side"] == "DOWN" and signal["signal_price"] < signal["start_price"])
                        ),
                        diagnostics,
                        created_at,
                        created_at,
                    ),
                )
                if cursor.rowcount:
                    self.db.execute(
                        """INSERT INTO strategy_mx_orders(
                               strategy, market_id, action, stage_key, status,
                               limit_price, requested_shares, filled_shares,
                               expires_elapsed, reason, created_at, updated_at
                           ) VALUES (?, ?, 'ENTRY', 'ENTRY', 'OPEN', ?, ?, 0, ?, ?, ?, ?)""",
                        (
                            strategy,
                            market_id,
                            MX_ENTRY_LIMIT,
                            requested_shares,
                            deadline,
                            "opening-window BUY LIMIT",
                            created_at,
                            created_at,
                        ),
                    )
            self._mx_intent_markets.add(intent_key)

    def _mx_sync_position_status(
        self, strategy: str, market_id: int, *, close_reason: str | None = None
    ) -> None:
        position = self.db.execute(
            """SELECT * FROM strategy_mx_positions
               WHERE strategy=? AND market_id=?""",
            (strategy, market_id),
        ).fetchone()
        entry = self.db.execute(
            """SELECT * FROM strategy_mx_orders
               WHERE strategy=? AND market_id=? AND action='ENTRY'
                 AND stage_key='ENTRY'""",
            (strategy, market_id),
        ).fetchone()
        if position is None or entry is None:
            return
        remaining = max(0.0, float(position["remaining_shares"]))
        filled_entry = float(position["filled_entry_shares"])
        entry_active = entry["status"] in {"OPEN", "PARTIAL"}
        terminal_statuses = {
            "SETTLED_WIN", "SETTLED_LOSS", "EXPIRED_UNFILLED",
            "CLOSED_TARGET", "CLOSED_REVERSAL", "CLOSED_REVERSAL_NO_FILL",
        }
        if position["status"] in terminal_statuses:
            return
        closed_at: str | None = None
        if remaining > 1e-12:
            status = "EXIT_OPEN" if position["reverse_triggered"] else "OPEN"
        elif entry_active:
            status = "ENTRY_OPEN"
        elif filled_entry <= 1e-12:
            status = (
                "CLOSED_REVERSAL_NO_FILL"
                if position["reverse_triggered"]
                else "EXPIRED_UNFILLED"
            )
            closed_at = utc_iso()
        else:
            status = (
                "CLOSED_REVERSAL"
                if position["reverse_triggered"]
                else "CLOSED_TARGET"
            )
            closed_at = utc_iso()
        self.db.execute(
            """UPDATE strategy_mx_positions
               SET status=?, close_reason=COALESCE(?, close_reason),
                   updated_at=?, closed_at=COALESCE(closed_at, ?)
               WHERE strategy=? AND market_id=?""",
            (
                status,
                close_reason,
                utc_iso(),
                closed_at,
                strategy,
                market_id,
            ),
        )

    def _mx_expire_entries(
        self, market_id: int, elapsed: float, *, force: bool = False
    ) -> None:
        rows = self.db.execute(
            """SELECT * FROM strategy_mx_orders
               WHERE market_id=? AND action='ENTRY' AND status IN ('OPEN','PARTIAL')""",
            (market_id,),
        ).fetchall()
        for order in rows:
            if not force and elapsed <= float(order["expires_elapsed"]):
                continue
            status = (
                "EXPIRED_PARTIAL"
                if float(order["filled_shares"]) > 1e-12
                else "EXPIRED_UNFILLED"
            )
            self.db.execute(
                """UPDATE strategy_mx_orders
                   SET status=?, reason='opening window expired', updated_at=?
                   WHERE id=?""",
                (status, utc_iso(), int(order["id"])),
            )
            if float(order["filled_shares"]) > 1e-12:
                position = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy=? AND market_id=?""",
                    (str(order["strategy"]), market_id),
                ).fetchone()
                if position is not None:
                    self._mx_finalize_exit_plan(position)
            self._mx_sync_position_status(
                str(order["strategy"]), market_id,
                close_reason="opening window expired",
            )

    def _mx_finalize_exit_plan(self, position: sqlite3.Row) -> None:
        """Create immutable exit quantities from the final actual entry VWAP."""
        strategy = str(position["strategy"])
        market_id = int(position["market_id"])
        filled_entry = float(position["filled_entry_shares"])
        if filled_entry <= 1e-12:
            return
        reference = float(position["entry_cost"]) / filled_entry
        self.db.execute(
            """UPDATE strategy_mx_positions
               SET reference_entry_price=?, updated_at=?
               WHERE strategy=? AND market_id=?""",
            (reference, utc_iso(), strategy, market_id),
        )
        variant = self._mx_variant(strategy)
        if strategy in MX_FIXED_TARGETS:
            stages = (("TARGET", MX_FIXED_TARGETS[strategy], 1.0),)
        elif variant == "P50":
            stages = (
                ("P50_150", min(1.0, reference * 1.5), 0.5),
                ("P50_200", min(1.0, reference * 2.0), 0.5),
            )
        elif variant == "P10":
            stages = tuple(
                (
                    f"P10_{int(round(multiplier * 100)):03d}",
                    min(1.0, reference * multiplier),
                    0.1,
                )
                for multiplier in (1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0)
            )
        else:
            stages = ()
        now = utc_iso()
        for stage_key, target, fraction in stages:
            allocation = filled_entry * fraction
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_mx_orders(
                       strategy, market_id, action, stage_key, status,
                       limit_price, requested_shares, filled_shares,
                       reason, created_at, updated_at
                   ) VALUES (?, ?, 'EXIT', ?, 'OPEN', ?, ?, 0, ?, ?, ?)""",
                (
                    strategy,
                    market_id,
                    stage_key,
                    target,
                    allocation,
                    "target exit based on final entry VWAP",
                    now,
                    now,
                ),
            )

    def _mx_insert_fill(
        self,
        order: sqlite3.Row,
        position: sqlite3.Row,
        *,
        price: float,
        shares: float,
        fee_bps: int,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        reason: str,
    ) -> bool:
        if shares <= 1e-12:
            return False
        gross = price * shares
        fee = taker_fee(shares, price, fee_bps)
        sequence = self._mx_event_sequence(snapshot, context)
        cursor = self.db.execute(
            """INSERT OR IGNORE INTO strategy_mx_fills(
                   order_id, strategy, market_id, action, stage_key, side,
                   price, shares, gross, fee, event_sequence,
                   received_monotonic_ns, timestamp, reason
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(order["id"]),
                str(position["strategy"]),
                int(position["market_id"]),
                str(order["action"]),
                str(order["stage_key"]),
                str(position["side"]),
                price,
                shares,
                gross,
                fee,
                sequence,
                context.get(
                    "received_monotonic_ns",
                    snapshot.get("received_monotonic_ns"),
                ),
                snapshot.get("timestamp") or utc_iso(),
                reason,
            ),
        )
        if not cursor.rowcount:
            return False
        new_filled = float(order["filled_shares"]) + shares
        requested = float(order["requested_shares"])
        self.db.execute(
            """UPDATE strategy_mx_orders
               SET filled_shares=?, status=?, updated_at=? WHERE id=?""",
            (
                new_filled,
                self._mx_order_status(requested, new_filled),
                utc_iso(),
                int(order["id"]),
            ),
        )
        strategy = str(position["strategy"])
        market_id = int(position["market_id"])
        if order["action"] == "ENTRY":
            self.db.execute(
                """UPDATE strategy_mx_positions
                   SET filled_entry_shares=filled_entry_shares+?,
                       entry_cost=entry_cost+?, entry_fees=entry_fees+?,
                       requested_exit_shares=requested_exit_shares+?,
                       remaining_shares=remaining_shares+?,
                       remaining_cost_basis=remaining_cost_basis+?,
                       updated_at=?
                   WHERE strategy=? AND market_id=?""",
                (
                    shares,
                    gross,
                    fee,
                    shares,
                    shares,
                    gross + fee,
                    utc_iso(),
                    strategy,
                    market_id,
                ),
            )
            refreshed = self.db.execute(
                """SELECT * FROM strategy_mx_positions
                   WHERE strategy=? AND market_id=?""",
                (strategy, market_id),
            ).fetchone()
            assert refreshed is not None
            if new_filled >= requested - 1e-12:
                self._mx_finalize_exit_plan(refreshed)
        else:
            remaining = float(position["remaining_shares"])
            basis_total = float(position["remaining_cost_basis"])
            allocated_basis = basis_total * min(1.0, shares / remaining)
            realized = gross - fee - allocated_basis
            self.db.execute(
                """UPDATE strategy_mx_positions
                   SET filled_exit_shares=filled_exit_shares+?,
                       exit_proceeds=exit_proceeds+?, exit_fees=exit_fees+?,
                       remaining_shares=MAX(0, remaining_shares-?),
                       remaining_cost_basis=MAX(0, remaining_cost_basis-?),
                       realized_pnl=realized_pnl+?, updated_at=?
                   WHERE strategy=? AND market_id=?""",
                (
                    shares,
                    gross,
                    fee,
                    shares,
                    allocated_basis,
                    realized,
                    utc_iso(),
                    strategy,
                    market_id,
                ),
            )
        self._mx_sync_position_status(strategy, market_id, close_reason=reason)
        return True

    def _mx_trigger_reversal(
        self, position: sqlite3.Row, *, reason: str
    ) -> None:
        if int(position["reverse_triggered"]):
            return
        strategy = str(position["strategy"])
        market_id = int(position["market_id"])
        entry = self.db.execute(
            """SELECT * FROM strategy_mx_orders
               WHERE strategy=? AND market_id=? AND action='ENTRY'
                 AND stage_key='ENTRY'""",
            (strategy, market_id),
        ).fetchone()
        now = utc_iso()
        if entry is not None and entry["status"] in {"OPEN", "PARTIAL"}:
            cancelled = (
                "CANCELLED_PARTIAL"
                if float(entry["filled_shares"]) > 1e-12
                else "CANCELLED_UNFILLED"
            )
            self.db.execute(
                """UPDATE strategy_mx_orders
                   SET status=?, reason=?, updated_at=? WHERE id=?""",
                (cancelled, reason, now, int(entry["id"])),
            )
        filled_entry = float(position["filled_entry_shares"])
        if filled_entry > 1e-12:
            self._mx_finalize_exit_plan(position)
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_mx_orders(
                       strategy, market_id, action, stage_key, status,
                       limit_price, requested_shares, filled_shares,
                       reason, created_at, updated_at
                   ) VALUES (?, ?, 'EXIT', 'REVERSAL', 'OPEN', NULL, ?, 0, ?, ?, ?)""",
                (strategy, market_id, filled_entry, reason, now, now),
            )
        self.db.execute(
            """UPDATE strategy_mx_positions
               SET reverse_triggered=1, close_reason=?, updated_at=?
               WHERE strategy=? AND market_id=?""",
            (reason, now, strategy, market_id),
        )
        self._mx_sync_position_status(strategy, market_id, close_reason=reason)

    @staticmethod
    def _mx_book(
        snapshot: dict[str, Any],
        side: str,
        cfg: dict[str, float | bool],
    ) -> dict[str, float] | None:
        side_key = side.lower()
        try:
            ask = float(snapshot[f"{side_key}_ask"])
            bid = float(snapshot[f"{side_key}_bid"])
            ask_size = float(snapshot[f"{side_key}_ask_size"])
            bid_size = float(snapshot[f"{side_key}_bid_size"])
            skew = float(snapshot["book_skew_ms"])
            age = float(snapshot["book_age_ms"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (
            all(
                math.isfinite(value)
                for value in (ask, bid, ask_size, bid_size, skew, age)
            )
            and 0 < ask <= 1
            and 0 <= bid <= ask
            and ask_size >= 0
            and bid_size >= 0
            and 0 <= skew <= float(cfg["strategy_m_max_book_skew_ms"])
            and 0 <= age <= float(cfg["strategy_m_max_book_age_ms"])
        ):
            return None
        return {
            "ask": ask,
            "bid": bid,
            "ask_size": ask_size,
            "bid_size": bid_size,
        }

    def _mx_process_prediction(
        self,
        snapshot: dict[str, Any],
        context: dict[str, Any],
        cfg: dict[str, float | bool],
        fee_bps: int,
        elapsed: float,
    ) -> None:
        if (
            self._m_event_type(context) != "prediction"
            or context.get("execution_eligible") is not True
        ):
            return
        market_id = int(snapshot["market_id"])
        positions = self.db.execute(
            """SELECT * FROM strategy_mx_positions
               WHERE market_id=? ORDER BY strategy""",
            (market_id,),
        ).fetchall()
        for initial in positions:
            strategy = str(initial["strategy"])
            book = self._mx_book(snapshot, str(initial["side"]), cfg)
            if book is None or not self._m_book_is_after_signal(
                initial["signal_received_monotonic_ns"], snapshot, context
            ):
                continue
            position = initial
            if (
                self._mx_variant(strategy) == "REV"
                and not int(position["reverse_triggered"])
                and float(position["filled_entry_shares"]) > 1e-12
                and int(position["rev_bid_armed"])
                and book["bid"] <= MX_ENTRY_LIMIT
            ):
                self._mx_trigger_reversal(
                    position,
                    reason="held-side bid crossed down to <= 0.50",
                )
                position = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy=? AND market_id=?""",
                    (strategy, market_id),
                ).fetchone()
                assert position is not None
            entry = self.db.execute(
                """SELECT * FROM strategy_mx_orders
                   WHERE strategy=? AND market_id=? AND action='ENTRY'
                     AND stage_key='ENTRY'""",
                (strategy, market_id),
            ).fetchone()
            if (
                entry is not None
                and entry["status"] in {"OPEN", "PARTIAL"}
                and not int(position["reverse_triggered"])
                and elapsed <= float(entry["expires_elapsed"])
                and book["ask"] <= MX_ENTRY_LIMIT
                and book["ask_size"] > 0
            ):
                requested_remaining = max(
                    0.0,
                    float(entry["requested_shares"])
                    - float(entry["filled_shares"]),
                )
                quantity = min(requested_remaining, book["ask_size"])
                self._mx_insert_fill(
                    entry,
                    position,
                    price=book["ask"],
                    shares=quantity,
                    fee_bps=fee_bps,
                    snapshot=snapshot,
                    context=context,
                    reason="opening-window limit fill",
                )

            position = self.db.execute(
                """SELECT * FROM strategy_mx_positions
                   WHERE strategy=? AND market_id=?""",
                (strategy, market_id),
            ).fetchone()
            assert position is not None
            if (
                self._mx_variant(strategy) == "REV"
                and not int(position["reverse_triggered"])
                and float(position["filled_entry_shares"]) > 1e-12
            ):
                if book["bid"] > MX_ENTRY_LIMIT:
                    self.db.execute(
                        """UPDATE strategy_mx_positions
                           SET rev_bid_armed=1, updated_at=?
                           WHERE strategy=? AND market_id=?""",
                        (utc_iso(), strategy, market_id),
                    )
                position = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy=? AND market_id=?""",
                    (strategy, market_id),
                ).fetchone()
                assert position is not None
            available_bid = book["bid_size"]
            if available_bid <= 0 or float(position["remaining_shares"]) <= 1e-12:
                continue
            if self._mx_variant(strategy) == "REV":
                if not int(position["reverse_triggered"]):
                    continue
                orders = self.db.execute(
                    """SELECT * FROM strategy_mx_orders
                       WHERE strategy=? AND market_id=? AND action='EXIT'
                         AND stage_key='REVERSAL' AND status IN ('OPEN','PARTIAL')""",
                    (strategy, market_id),
                ).fetchall()
            else:
                orders = self.db.execute(
                    """SELECT * FROM strategy_mx_orders
                       WHERE strategy=? AND market_id=? AND action='EXIT'
                         AND status IN ('OPEN','PARTIAL') AND limit_price<=?
                       ORDER BY limit_price ASC, id ASC""",
                    (strategy, market_id, book["bid"] + 1e-12),
                ).fetchall()
            for order in orders:
                if available_bid <= 1e-12:
                    break
                position = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy=? AND market_id=?""",
                    (strategy, market_id),
                ).fetchone()
                assert position is not None
                order_remaining = max(
                    0.0,
                    float(order["requested_shares"])
                    - float(order["filled_shares"]),
                )
                quantity = min(
                    order_remaining,
                    float(position["remaining_shares"]),
                    available_bid,
                )
                if self._mx_insert_fill(
                    order,
                    position,
                    price=book["bid"],
                    shares=quantity,
                    fee_bps=fee_bps,
                    snapshot=snapshot,
                    context=context,
                    reason=(
                        "reversal exit"
                        if self._mx_variant(strategy) == "REV"
                        else f"target reached {float(order['limit_price']):.4f}"
                    ),
                ):
                    available_bid -= quantity

    def process_mx_event(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> None:
        """Process independent M-exit experiments from the realtime WS path."""
        if realtime_context is None:
            return
        context = dict(realtime_context)
        try:
            market_id = int(snapshot["market_id"])
            elapsed = 300.0 - float(snapshot["seconds_left"])
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(elapsed) or elapsed < 0 or elapsed > 300:
            return
        event_type = self._m_event_type(context)
        if event_type == "spot" and market_id in self._mx_spot_quiet_markets:
            return
        cfg = self.config()
        with self.lock:
            changes_before = self.db.total_changes
            if event_type == "spot":
                self._mx_create_intents(snapshot, context, cfg)
            if (
                elapsed > float(cfg["strategy_m_entry_window_seconds"])
                and market_id not in self._mx_entry_expired_markets
            ):
                self._mx_expire_entries(market_id, elapsed)
                self._mx_entry_expired_markets.add(market_id)

            if event_type == "spot":
                rev_positions = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy IN ('MX_REV','M0X_REV') AND market_id=?""",
                    (market_id,),
                ).fetchall()
                for position in rev_positions:
                    if int(position["reverse_triggered"]):
                        continue
                    received_ns = context.get(
                        "received_monotonic_ns",
                        snapshot.get("received_monotonic_ns"),
                    )
                    try:
                        is_later = int(received_ns) > int(
                            position["signal_received_monotonic_ns"]
                        )
                        spot = float(snapshot["spot_price"])
                        start = float(position["start_price"])
                    except (TypeError, ValueError, KeyError):
                        is_later = False
                        spot = start = 0.0
                    reversed_side = (
                        (position["side"] == "UP" and spot <= start)
                        or (position["side"] == "DOWN" and spot >= start)
                    )
                    chosen_side = (
                        (position["side"] == "UP" and spot > start)
                        or (position["side"] == "DOWN" and spot < start)
                    )
                    was_armed = bool(position["rev_spot_armed"])
                    if is_later and not was_armed and chosen_side:
                        self.db.execute(
                            """UPDATE strategy_mx_positions
                               SET rev_spot_armed=1, updated_at=?
                               WHERE strategy=? AND market_id=?""",
                            (utc_iso(), str(position["strategy"]), market_id),
                        )
                    elif is_later and was_armed and reversed_side:
                        self._mx_trigger_reversal(
                            position,
                            reason="Spot crossed official startPrice in reverse",
                        )
                rev_positions = self.db.execute(
                    """SELECT * FROM strategy_mx_positions
                       WHERE strategy IN ('MX_REV','M0X_REV') AND market_id=?""",
                    (market_id,),
                ).fetchall()
                quiet_candidate = bool(
                    market_id in self._mx_entry_expired_markets
                    and all(
                        int(position["reverse_triggered"])
                        or str(position["status"]).startswith(
                            ("CLOSED", "SETTLED", "EXPIRED")
                        )
                        or float(position["filled_entry_shares"]) <= 1e-12
                        for position in rev_positions
                    )
                )
                if quiet_candidate:
                    active_entry = self.db.execute(
                        """SELECT 1 FROM strategy_mx_orders
                           WHERE market_id=? AND action='ENTRY'
                             AND status IN ('OPEN','PARTIAL') LIMIT 1""",
                        (market_id,),
                    ).fetchone()
                    if active_entry is None:
                        self._mx_spot_quiet_markets.add(market_id)
            elif event_type == "prediction":
                self._mx_process_prediction(
                    snapshot, context, cfg, int(fee_bps), elapsed
                )
            if self.db.total_changes != changes_before:
                self.db.commit()

    def process_pair_arb_snapshot(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate synchronized, independent UP and DOWN token books.

        The M-series realtime stream normalizes one UP book and derives the
        opposite quotes from its bid/ask.  Such a synthetic pair can never
        expose a buy-both-outcomes opportunity, so this experiment accepts
        only the Collector's independently requested outcome-token books.
        """
        context = dict(realtime_context or {})
        if (
            context.get("trigger_source") != "dual_token_rest"
            or context.get("pair_book_source") != "independent_outcome_books"
            or context.get("execution_eligible") is not True
            or context.get("market_data_integrity_ok") is not True
        ):
            return []
        try:
            topic_id = int(snapshot["topic_id"])
            market_id = int(snapshot["market_id"])
        except (KeyError, TypeError, ValueError):
            return []

        def record_evaluation(
            reason: str,
            *,
            valid_book: bool = False,
            net_edge: float | None = None,
            up_ask: float | None = None,
            down_ask: float | None = None,
            book_skew_ms: float | None = None,
            book_age_ms: float | None = None,
        ) -> None:
            evaluated_at = utc_iso()
            eligible_010 = int(
                net_edge is not None and net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_010"]
            )
            eligible_qc_015 = int(
                net_edge is not None
                and net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_QC_015"]
            )
            eligible_020 = int(
                net_edge is not None and net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_020"]
            )
            with self.lock:
                self.db.execute(
                    """INSERT INTO strategy_pair_arb_market_stats (
                           market_id, topic_id, first_evaluated_at,
                           last_evaluated_at, evaluations,
                           valid_book_evaluations, eligible_010_snapshots,
                           eligible_qc_015_snapshots, eligible_020_snapshots,
                           rejected_invalid_book,
                           rejected_book_skew, rejected_book_age, rejected_edge,
                           best_net_edge, last_net_edge, last_up_ask,
                           last_down_ask, last_book_skew_ms, last_book_age_ms,
                           last_reason, book_source
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(market_id) DO UPDATE SET
                           topic_id=excluded.topic_id,
                           last_evaluated_at=excluded.last_evaluated_at,
                           evaluations=strategy_pair_arb_market_stats.evaluations+1,
                           valid_book_evaluations=strategy_pair_arb_market_stats.valid_book_evaluations+excluded.valid_book_evaluations,
                           eligible_010_snapshots=strategy_pair_arb_market_stats.eligible_010_snapshots+excluded.eligible_010_snapshots,
                           eligible_qc_015_snapshots=strategy_pair_arb_market_stats.eligible_qc_015_snapshots+excluded.eligible_qc_015_snapshots,
                           eligible_020_snapshots=strategy_pair_arb_market_stats.eligible_020_snapshots+excluded.eligible_020_snapshots,
                           rejected_invalid_book=strategy_pair_arb_market_stats.rejected_invalid_book+excluded.rejected_invalid_book,
                           rejected_book_skew=strategy_pair_arb_market_stats.rejected_book_skew+excluded.rejected_book_skew,
                           rejected_book_age=strategy_pair_arb_market_stats.rejected_book_age+excluded.rejected_book_age,
                           rejected_edge=strategy_pair_arb_market_stats.rejected_edge+excluded.rejected_edge,
                           best_net_edge=CASE
                               WHEN excluded.best_net_edge IS NULL THEN strategy_pair_arb_market_stats.best_net_edge
                               WHEN strategy_pair_arb_market_stats.best_net_edge IS NULL
                                    OR excluded.best_net_edge > strategy_pair_arb_market_stats.best_net_edge
                               THEN excluded.best_net_edge
                               ELSE strategy_pair_arb_market_stats.best_net_edge
                           END,
                           last_net_edge=excluded.last_net_edge,
                           last_up_ask=excluded.last_up_ask,
                           last_down_ask=excluded.last_down_ask,
                           last_book_skew_ms=excluded.last_book_skew_ms,
                           last_book_age_ms=excluded.last_book_age_ms,
                           last_reason=excluded.last_reason,
                           book_source=excluded.book_source""",
                    (
                        market_id, topic_id, evaluated_at, evaluated_at, 1,
                        int(valid_book), eligible_010, eligible_qc_015,
                        eligible_020,
                        int(reason == "INVALID_BOOK"),
                        int(reason == "BOOK_SKEW_EXCEEDED"),
                        int(reason == "BOOK_AGE_EXCEEDED"),
                        int(reason.startswith("EDGE_BELOW")),
                        net_edge, net_edge, up_ask, down_ask,
                        book_skew_ms, book_age_ms, reason,
                        "independent_outcome_books",
                    ),
                )
                self.db.commit()

        try:
            seconds_left = float(snapshot["seconds_left"])
            up_ask = float(snapshot["up_ask"])
            down_ask = float(snapshot["down_ask"])
            up_size = float(snapshot["up_ask_size"])
            down_size = float(snapshot["down_ask_size"])
            book_skew_ms = float(snapshot["book_skew_ms"])
            book_age_ms = float(snapshot["book_age_ms"])
            fee_rate_bps = int(fee_bps)
        except (KeyError, TypeError, ValueError):
            record_evaluation("INVALID_BOOK")
            return []
        numeric_values = (
            seconds_left, up_ask, down_ask, up_size, down_size,
            book_skew_ms, book_age_ms,
        )
        if (
            not all(math.isfinite(value) for value in numeric_values)
            or not 0 < up_ask <= 1
            or not 0 < down_ask <= 1
            or up_size <= 0
            or down_size <= 0
            or not 0 <= seconds_left <= 300
            or book_skew_ms < 0
            or book_age_ms < 0
            or fee_rate_bps < 0
        ):
            record_evaluation(
                "INVALID_BOOK", up_ask=up_ask, down_ask=down_ask,
                book_skew_ms=book_skew_ms, book_age_ms=book_age_ms,
            )
            return []

        cfg = self.config()
        if book_skew_ms > float(cfg["strategy_pair_arb_max_book_skew_ms"]):
            record_evaluation(
                "BOOK_SKEW_EXCEEDED", up_ask=up_ask, down_ask=down_ask,
                book_skew_ms=book_skew_ms, book_age_ms=book_age_ms,
            )
            return []
        if book_age_ms > float(cfg["strategy_pair_arb_max_book_age_ms"]):
            record_evaluation(
                "BOOK_AGE_EXCEEDED", up_ask=up_ask, down_ask=down_ask,
                book_skew_ms=book_skew_ms, book_age_ms=book_age_ms,
            )
            return []
        unit_up_fee = taker_fee(1.0, up_ask, fee_rate_bps)
        unit_down_fee = taker_fee(1.0, down_ask, fee_rate_bps)
        net_edge = 1.0 - up_ask - down_ask - unit_up_fee - unit_down_fee
        if not math.isfinite(net_edge):
            record_evaluation(
                "INVALID_BOOK", up_ask=up_ask, down_ask=down_ask,
                book_skew_ms=book_skew_ms, book_age_ms=book_age_ms,
            )
            return []
        up_levels = _normalized_ask_levels(
            snapshot.get("_up_asks"),
            fallback_price=up_ask,
            fallback_size=up_size,
        )
        down_levels = _normalized_ask_levels(
            snapshot.get("_down_asks"),
            fallback_price=down_ask,
            fallback_size=down_size,
        )
        if net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_020"]:
            evaluation_reason = "ELIGIBLE_020"
        elif net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_QC_015"]:
            evaluation_reason = "ELIGIBLE_QC_015"
        elif net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_010"]:
            evaluation_reason = "ELIGIBLE_010"
        elif net_edge + 1e-12 >= PAIR_ARB_THRESHOLDS["PAIR_ARB_RISK_020"]:
            evaluation_reason = "ELIGIBLE_RISK_020"
        else:
            evaluation_reason = "EDGE_BELOW_RISK_020"
        record_evaluation(
            evaluation_reason, valid_book=True, net_edge=net_edge,
            up_ask=up_ask, down_ask=down_ask,
            book_skew_ms=book_skew_ms, book_age_ms=book_age_ms,
        )
        opened_at = utc_iso()
        signal_timestamp = str(snapshot.get("timestamp") or opened_at)

        live_candidates: list[dict[str, Any]] = []
        with self.lock:
            changes_before = self.db.total_changes
            for strategy, minimum_edge in PAIR_ARB_THRESHOLDS.items():
                enabled_key = f"strategy_{strategy.lower()}_enabled"
                if not bool(cfg[enabled_key]) or net_edge + 1e-12 < minimum_edge:
                    continue
                fill = pair_orderbook_fill(
                    up_levels,
                    down_levels,
                    stake=float(cfg["strategy_pair_arb_stake"]),
                    fee_bps=fee_rate_bps,
                    minimum_edge=minimum_edge,
                )
                if fill is None:
                    continue
                requested_shares = float(fill["requested_shares"])
                shares = float(fill["filled_shares"])
                up_fee = float(fill["up_fee"])
                down_fee = float(fill["down_fee"])
                total_cost = float(fill["total_cost"])
                payout = float(fill["payout"])
                locked_pnl = float(fill["locked_pnl"])
                executed_net_edge = float(fill["net_edge_per_share"])
                diagnostics = {
                    "atomic_execution_assumed": False,
                    "execution_eligible": True,
                    "book_source": "independent_outcome_books",
                    "fill_model": "dual_token_orderbook_depth_vwap_partial_fill_v1",
                    "market_data_integrity_ok": True,
                    "minimum_net_edge_per_share": minimum_edge,
                    "requested_shares": requested_shares,
                    "filled_shares": shares,
                    "fill_ratio": float(fill["fill_ratio"]),
                    "partial_fill": bool(fill["partial_fill"]),
                    "up_fill_vwap": float(fill["up_vwap"]),
                    "down_fill_vwap": float(fill["down_vwap"]),
                    "up_levels_consumed": int(fill["up_levels_consumed"]),
                    "down_levels_consumed": int(fill["down_levels_consumed"]),
                    "paper_only": True,
                    "signal_event_sequence": context.get("trigger_event_sequence"),
                    "slippage_stress_per_leg": [0.005, 0.01],
                }
                cursor = self.db.execute(
                    """INSERT OR IGNORE INTO strategy_pair_arb_trades (
                           strategy, topic_id, market_id, opened_at,
                           signal_timestamp, seconds_left, up_ask, down_ask,
                           up_ask_size, down_ask_size, requested_shares, shares,
                           fill_ratio, partial_fill, up_fill_vwap, down_fill_vwap,
                           up_levels_consumed, down_levels_consumed, up_fee, down_fee,
                           total_cost, payout, locked_pnl, net_edge_per_share,
                           stressed_pnl_005, stressed_pnl_010, book_skew_ms,
                           book_age_ms, fee_rate_bps, diagnostics_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        strategy, topic_id, market_id, opened_at,
                        signal_timestamp, seconds_left, up_ask, down_ask,
                        up_size, down_size, requested_shares, shares,
                        float(fill["fill_ratio"]), int(bool(fill["partial_fill"])),
                        float(fill["up_vwap"]), float(fill["down_vwap"]),
                        int(fill["up_levels_consumed"]),
                        int(fill["down_levels_consumed"]), up_fee, down_fee,
                        total_cost, payout, locked_pnl, executed_net_edge,
                        locked_pnl - shares * 0.010,
                        locked_pnl - shares * 0.020,
                        book_skew_ms, book_age_ms, fee_rate_bps,
                        json.dumps(diagnostics, sort_keys=True),
                    ),
                )
                if cursor.rowcount:
                    pair_total_price = up_ask + down_ask
                    for side, entry_price in (("UP", up_ask), ("DOWN", down_ask)):
                        live_candidates.append({
                            "strategy": strategy,
                            "topic_id": topic_id,
                            "market_id": market_id,
                            "side": side,
                            "entry_price": entry_price,
                            "pair_total_price": pair_total_price,
                            "pair_up_price": up_ask,
                            "pair_down_price": down_ask,
                            "pair_arb_leg": True,
                            "pair_book_source": "independent_outcome_books",
                            "pair_max_book_age_ms": float(
                                cfg["strategy_pair_arb_max_book_age_ms"]
                            ),
                            "pair_max_book_skew_ms": float(
                                cfg["strategy_pair_arb_max_book_skew_ms"]
                            ),
                            "market_data_integrity_ok": True,
                            "signal_timestamp": signal_timestamp,
                            "market_event_received_monotonic_ns": snapshot.get(
                                "received_monotonic_ns"
                            ),
                            "drawdown_control_start_price": snapshot.get(
                                "start_price"
                            ),
                            "drawdown_control_spot_price": snapshot.get(
                                "spot_price"
                            ),
                            "drawdown_control_spot_age_ms": snapshot.get(
                                "spot_age_ms"
                            ),
                        })
            if self.db.total_changes != changes_before:
                self.db.commit()
        return live_candidates

    def _record_m01o_gate_decision(
        self,
        *,
        strategy: str,
        profile: str,
        snapshot: dict[str, Any],
        candidate: dict[str, Any],
        gate: dict[str, Any],
        fee_bps: int,
        opened: bool,
    ) -> None:
        """Persist one causal filter decision without creating a trade."""
        market_id = int(snapshot["market_id"])
        decision = "ALLOW" if gate.get("allowed") is True else "BLOCK"
        category = str(
            gate.get("blockCategory")
            or ("ALLOW" if decision == "ALLOW" else "FILTERED")
        )
        fingerprint = (decision, category, bool(opened))
        cache_key = (strategy, market_id)
        if self._m01o_gate_decision_cache.get(cache_key) == fingerprint:
            return
        observed_at = str(snapshot.get("timestamp") or utc_iso())
        entry = float(candidate["entry"])
        filled_stake = float(candidate["filled_stake"])
        fees = taker_fee(
            float(candidate["filled_shares"]), entry, int(fee_bps)
        )
        self.db.execute(
            """INSERT INTO strategy_m01o_gate_decisions(
                   strategy, profile, market_id, topic_id, side,
                   candidate_entry_price, candidate_stake, candidate_fees,
                   first_evaluated_at, last_evaluated_at, evaluations,
                   last_decision, last_block_category, ever_allowed, opened,
                   gate_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
               ON CONFLICT(strategy, market_id) DO UPDATE SET
                   last_evaluated_at=excluded.last_evaluated_at,
                   evaluations=strategy_m01o_gate_decisions.evaluations + 1,
                   last_decision=excluded.last_decision,
                   last_block_category=excluded.last_block_category,
                   ever_allowed=MAX(
                       strategy_m01o_gate_decisions.ever_allowed,
                       excluded.ever_allowed
                   ),
                   opened=MAX(
                       strategy_m01o_gate_decisions.opened,
                       excluded.opened
                   ),
                   gate_json=excluded.gate_json""",
            (
                strategy,
                profile,
                market_id,
                int(snapshot["topic_id"]),
                str(candidate["side"]),
                entry,
                filled_stake,
                fees,
                observed_at,
                observed_at,
                decision,
                category,
                int(gate.get("allowed") is True),
                int(bool(opened)),
                json.dumps(gate, sort_keys=True, default=str),
            ),
        )
        self._m01o_gate_decision_cache[cache_key] = fingerprint

    def m01o_filter_experiment_state(self) -> dict[str, Any]:
        """Compare F2/F1/LIVE only where their shared M01 candidate existed."""
        profiles = (
            ("M01O", "F2", "嚴格組"),
            ("M01O_F1", "F1", "寬鬆組"),
            ("M01O_LIVE", "LIVE", "當輪優先組"),
        )
        taipei = timezone(timedelta(hours=8))
        groups: dict[str, Any] = {}
        with self.lock:
            for strategy, profile, label in profiles:
                reset_row = self.db.execute(
                    """SELECT reset_at FROM strategy_measurement_resets
                       WHERE strategy=? ORDER BY id DESC LIMIT 1""",
                    (strategy,),
                ).fetchone()
                reset_at = str(reset_row["reset_at"]) if reset_row else None
                rows = self.db.execute(
                    """SELECT decisions.*,
                              base.status AS base_status,
                              base.pnl AS base_pnl,
                              base.entry_price AS base_entry_price,
                              base.opened_at AS base_opened_at,
                              filtered.status AS filtered_status,
                              filtered.pnl AS filtered_pnl,
                              filtered.entry_price AS filtered_entry_price,
                              filtered.opened_at AS filtered_opened_at
                         FROM strategy_m01o_gate_decisions AS decisions
                         LEFT JOIN trades AS base
                           ON base.market_id=decisions.market_id
                          AND base.strategy='M01'
                         LEFT JOIN trades AS filtered
                           ON filtered.market_id=decisions.market_id
                          AND filtered.strategy=decisions.strategy
                        WHERE decisions.strategy=?
                          AND (? IS NULL OR decisions.first_evaluated_at>=?)
                        ORDER BY decisions.first_evaluated_at ASC""",
                    (strategy, reset_at, reset_at),
                ).fetchall()
                values = [dict(row) for row in rows]
                candidate_markets = len(values)
                allowed_markets = sum(int(row["ever_allowed"]) for row in values)
                opened_rows = [row for row in values if int(row["opened"])]
                settled_rows = [
                    row
                    for row in opened_rows
                    if str(row.get("filtered_status") or "")
                    in {"SETTLED_WIN", "SETTLED_LOSS"}
                    and row.get("filtered_pnl") is not None
                ]
                wins = sum(
                    str(row["filtered_status"]) == "SETTLED_WIN"
                    for row in settled_rows
                )
                losses = len(settled_rows) - wins
                pnl_values = [float(row["filtered_pnl"]) for row in settled_rows]
                gross_profit = sum(value for value in pnl_values if value > 0)
                gross_loss = abs(sum(value for value in pnl_values if value < 0))
                cumulative = 0.0
                peak = 0.0
                max_drawdown = 0.0
                for value in pnl_values:
                    cumulative += value
                    peak = max(peak, cumulative)
                    max_drawdown = max(max_drawdown, peak - cumulative)
                candidate_days = {
                    datetime.fromisoformat(
                        str(row["first_evaluated_at"]).replace("Z", "+00:00")
                    ).astimezone(taipei).date().isoformat()
                    for row in values
                }
                trade_days = {
                    datetime.fromisoformat(
                        str(row["first_evaluated_at"]).replace("Z", "+00:00")
                    ).astimezone(taipei).date().isoformat()
                    for row in opened_rows
                }
                blocked_counterfactual = [
                    row
                    for row in values
                    if not int(row["opened"])
                    and str(row.get("base_status") or "")
                    in {"SETTLED_WIN", "SETTLED_LOSS"}
                ]
                blocked_wins = sum(
                    str(row["base_status"]) == "SETTLED_WIN"
                    for row in blocked_counterfactual
                )
                blocked_losses = len(blocked_counterfactual) - blocked_wins
                block_categories = {
                    category: sum(
                        not int(row["opened"])
                        and str(row["last_block_category"]) == category
                        for row in values
                    )
                    for category in (
                        "NOT_READY",
                        "MISSING_OR_STALE",
                        "FILTERED",
                    )
                }
                settled_candidates = sum(
                    str(row.get("base_status") or "")
                    in {"SETTLED_WIN", "SETTLED_LOSS"}
                    for row in values
                )
                groups[profile] = {
                    "strategyId": strategy,
                    "profile": profile,
                    "label": label,
                    "resetAt": reset_at,
                    "candidateMarkets": candidate_markets,
                    "settledCandidateMarkets": settled_candidates,
                    "allowedMarkets": allowed_markets,
                    "openedTrades": len(opened_rows),
                    "tradeCoverageRate": (
                        len(opened_rows) / candidate_markets
                        if candidate_markets else None
                    ),
                    "actualFillRate": (
                        len(opened_rows) / allowed_markets
                        if allowed_markets else None
                    ),
                    "settledTrades": len(settled_rows),
                    "wins": wins,
                    "losses": losses,
                    "winRate": wins / len(settled_rows) if settled_rows else None,
                    "averageEntryPrice": (
                        sum(float(row["filtered_entry_price"]) for row in opened_rows)
                        / len(opened_rows)
                        if opened_rows else None
                    ),
                    "averagePnl": (
                        sum(pnl_values) / len(pnl_values) if pnl_values else None
                    ),
                    "realizedPnl": sum(pnl_values),
                    "grossProfit": gross_profit,
                    "grossLoss": gross_loss,
                    "profitFactor": (
                        gross_profit / gross_loss if gross_loss > 0 else None
                    ),
                    "profitFactorInfinite": bool(
                        gross_profit > 0 and gross_loss == 0
                    ),
                    "maxDrawdown": max_drawdown,
                    "observedDays": len(candidate_days),
                    "zeroTradeDays": len(candidate_days - trade_days),
                    "zeroTradeDayRate": (
                        len(candidate_days - trade_days) / len(candidate_days)
                        if candidate_days else None
                    ),
                    "blockedCounterfactualSettled": len(
                        blocked_counterfactual
                    ),
                    "blockedCounterfactualWins": blocked_wins,
                    "blockedCounterfactualLosses": blocked_losses,
                    "blockedCounterfactualWinRate": (
                        blocked_wins / len(blocked_counterfactual)
                        if blocked_counterfactual else None
                    ),
                    "blockedCounterfactualPnl": sum(
                        float(row["base_pnl"] or 0.0)
                        for row in blocked_counterfactual
                    ),
                    "blockCategories": block_categories,
                }
        return {
            "status": "COLLECTING" if any(
                group["candidateMarkets"] < 200 for group in groups.values()
            ) else "ANALYZABLE",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "candidateBasis": "same executable M01 direction and Ask candidate",
            "costBasis": "paper PnL after configured taker fees",
            "sampleTarget": {"minimum": 200, "preferred": 300},
            "groups": groups,
        }

    def _research_open_exposure(self) -> float:
        placeholders = ",".join("?" for _ in PRIMARY_RESEARCH_STRATEGIES)
        row = self.db.execute(
            f"SELECT COALESCE(SUM(stake), 0) FROM trades "
            f"WHERE status='OPEN' AND strategy IN ({placeholders})",
            PRIMARY_RESEARCH_STRATEGIES,
        ).fetchone()
        return float(row[0] or 0.0)

    def _research_shadow_open_exposure(self) -> float:
        placeholders = ",".join("?" for _ in SHADOW_RESEARCH_STRATEGIES)
        row = self.db.execute(
            f"SELECT COALESCE(SUM(stake), 0) FROM trades "
            f"WHERE status='OPEN' AND strategy IN ({placeholders})",
            SHADOW_RESEARCH_STRATEGIES,
        ).fetchone()
        return float(row[0] or 0.0)

    def _research_reserved_shares(
        self, market_id: int, side: str
    ) -> float:
        placeholders = ",".join("?" for _ in PRIMARY_RESEARCH_STRATEGIES)
        row = self.db.execute(
            f"SELECT COALESCE(SUM(shares), 0) FROM trades "
            f"WHERE market_id=? AND side=? AND status='OPEN' "
            f"AND strategy IN ({placeholders})",
            (market_id, side, *PRIMARY_RESEARCH_STRATEGIES),
        ).fetchone()
        return float(row[0] or 0.0)

    def _research_continuous_calibration_history(
        self,
        source_strategy: str,
        *,
        before_market_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return only earlier source trades with an official known outcome."""
        rows = self.db.execute(
            """SELECT t.market_id, t.side, t.model_probability,
                      t.diagnostics_json, s.official_winner
                 FROM trades AS t
                 JOIN market_settlements AS s ON s.market_id=t.market_id
                WHERE t.strategy=? AND t.market_id < ?
                  AND s.status='OFFICIAL' AND s.official_winner IS NOT NULL
                ORDER BY t.market_id DESC, t.id DESC
                LIMIT ?""",
            (source_strategy, int(before_market_id), max(1, int(limit))),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                diagnostics = json.loads(str(row["diagnostics_json"] or "{}"))
                signal = float(diagnostics["signal"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            side = str(row["side"])
            history.append(
                {
                    "market_id": int(row["market_id"]),
                    "side": side,
                    "signal": signal,
                    "model_probability": row["model_probability"],
                    "won": int(side == str(row["official_winner"])),
                }
            )
        return history

    def _research_continuous_calibration_state(
        self, strategy: str
    ) -> dict[str, Any]:
        params = RESEARCH_PARAMETERS[strategy]
        source_strategy = CONTINUOUS_CALIBRATION_RULES[strategy]
        history = self._research_continuous_calibration_history(
            source_strategy,
            before_market_id=2**63 - 1,
            limit=int(params["history_window"]),
        )
        minimum = int(params["min_history"])
        return {
            "status": "READY" if len(history) >= minimum else "WARMUP",
            "sourceStrategy": source_strategy,
            "officialSourceSamples": len(history),
            "officialSourceWins": sum(int(row["won"]) for row in history),
            "minimumHistory": minimum,
            "minimumBucketHistory": int(params["min_bucket_history"]),
            "historyWindow": int(params["history_window"]),
            "priorStrength": float(params["prior_strength"]),
            "minimumEdge": float(params["min_edge"]),
            "officialOnly": True,
            "causalNextMarketOnly": True,
        }

    def _research_observer_auto_v6_history(
        self,
        source_strategy: str,
        *,
        before_market_id: int,
    ) -> list[dict[str, Any]]:
        """Return prior official source results with frozen V6 decisions."""
        rows = self.db.execute(
            """SELECT t.market_id, t.stake, t.pnl, t.diagnostics_json
                 FROM trades AS t
                 JOIN market_settlements AS s ON s.market_id=t.market_id
                WHERE t.strategy=? AND t.market_id < ?
                  AND s.status='OFFICIAL' AND s.official_winner IS NOT NULL
                  AND t.status IN ('SETTLED_WIN', 'SETTLED_LOSS')
                  AND t.pnl IS NOT NULL AND t.stake > 0
                ORDER BY t.market_id DESC, t.id DESC
                LIMIT ?""",
            (
                source_strategy,
                int(before_market_id),
                OBSERVER_AUTO_V6_SLOW_WINDOW * 5,
            ),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            try:
                diagnostics = json.loads(str(row["diagnostics_json"] or "{}"))
                context = diagnostics["realtime_context"]
                gates = context["m01o_observer_gates"]
                gate = gates["F1"]
                market_id = int(row["market_id"])
                sample_count = int(gate["historicalSampleCount"])
                minimum_samples = int(gate.get("minSettledSamples") or 6)
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue
            if (
                not isinstance(gate, dict)
                or str(gate.get("profile") or "").upper() != "F1"
                or str(gate.get("dataQualityStatus") or "").upper() != "READY"
                or sample_count < minimum_samples
            ):
                continue
            decision = futures_lead_observer_decision(
                "V6", gate, expected_market_id=market_id
            )
            history.append(
                {
                    "market_id": market_id,
                    "unit_pnl": float(row["pnl"]) / float(row["stake"]),
                    "v6_allowed": bool(decision["allowed"]),
                }
            )
            if len(history) >= OBSERVER_AUTO_V6_SLOW_WINDOW:
                break
        return list(reversed(history))

    def _research_observer_auto_v6_state(
        self, strategy: str
    ) -> dict[str, Any]:
        source_strategy = OBSERVER_AUTO_V6_STRATEGY_RULES[strategy]
        history = self._research_observer_auto_v6_history(
            source_strategy,
            before_market_id=2**63 - 1,
        )
        return {
            **observer_v6_auto_decision(history, None),
            "strategy": strategy,
            "sourceStrategy": source_strategy,
        }

    @staticmethod
    def _research_experiment_segment(sample_index: int) -> str:
        if sample_index <= 60:
            return "development"
        if sample_index <= 80:
            return "validation"
        if sample_index <= 100:
            return "holdout"
        if sample_index <= 200:
            return "confirmation"
        return "post_confirmation"

    def _research_experiment_next_sample(self, strategy: str) -> tuple[int, str]:
        sample_index = int(
            self.db.execute(
                "SELECT COUNT(*) + 1 FROM trades WHERE strategy=?",
                (strategy,),
            ).fetchone()[0]
        )
        return sample_index, self._research_experiment_segment(sample_index)

    def _research_experiment_validation_state(self, strategy: str) -> dict[str, Any]:
        rows = self.db.execute(
            "SELECT id, pnl FROM trades WHERE strategy=? ORDER BY id ASC",
            (strategy,),
        ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for name, start, end in (
            ("development", 1, 60),
            ("validation", 61, 80),
            ("holdout", 81, 100),
            ("confirmation", 101, 200),
        ):
            selected = rows[start - 1 : end]
            settled = [float(row["pnl"]) for row in selected if row["pnl"] is not None]
            groups[name] = {
                "target": end - start + 1,
                "samples": len(selected),
                "settled": len(settled),
                "wins": sum(value > 0 for value in settled),
                "losses": sum(value <= 0 for value in settled),
                "realizedPnl": sum(settled),
            }
        samples = len(rows)
        status = (
            "COLLECTING_MINIMUM"
            if samples < 100
            else "MINIMUM_COMPLETE_CONFIRMING"
            if samples < 200
            else "ANALYZABLE"
        )
        return {
            "status": status,
            "samples": samples,
            "minimum": 100,
            "preferred": 200,
            "splitRule": (
                "fixed chronological executable samples: 1-60 development, "
                "61-80 validation, 81-100 holdout, 101-200 frozen confirmation"
            ),
            "thresholdsFrozen": True,
            "groups": groups,
        }

    def _evaluate_research_time_exits(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        cfg: dict[str, float | bool],
        context: dict[str, Any],
    ) -> None:
        """Close fixed-time paper shadows only on a fresh, full-depth bid."""
        if context.get("market_data_integrity_ok", True) is not True:
            return
        market_id = int(snapshot["market_id"])
        placeholders = ",".join("?" for _ in FUTURES_LEAD_EXIT_STRATEGIES)
        rows = self.db.execute(
            f"SELECT * FROM trades WHERE market_id=? AND status='OPEN' "
            f"AND strategy IN ({placeholders}) ORDER BY id ASC",
            (market_id, *FUTURES_LEAD_EXIT_STRATEGIES),
        ).fetchall()
        if not rows:
            return
        now_seconds = _timestamp_seconds(snapshot.get("timestamp"))
        if now_seconds is None:
            return
        for trade in rows:
            try:
                diagnostics = json.loads(str(trade["diagnostics_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            entry_seconds = _timestamp_seconds(diagnostics.get("signal_timestamp"))
            if entry_seconds is None:
                continue
            params = RESEARCH_PARAMETERS[str(trade["strategy"])]
            elapsed = now_seconds - entry_seconds
            exit_after = float(params["exit_after_seconds"])
            exit_deadline = exit_after + float(params["exit_grace_seconds"])
            if not exit_after <= elapsed <= exit_deadline:
                continue
            side_key = str(trade["side"]).lower()
            try:
                bid = float(snapshot[f"{side_key}_bid"])
                visible_bid_size = float(snapshot[f"{side_key}_bid_size"])
                book_age_ms = float(snapshot["book_age_ms"])
                book_skew_ms = float(snapshot["book_skew_ms"])
            except (KeyError, TypeError, ValueError):
                continue
            max_book_age_ms = min(
                float(cfg["strategy_research_max_book_age_ms"]),
                float(params["max_prediction_age_ms"]),
            )
            shares = float(trade["shares"])
            if not (
                0 < bid < 1
                and visible_bid_size + 1e-12 >= shares
                and 0 <= book_age_ms <= max_book_age_ms
                and 0 <= book_skew_ms
                <= float(cfg["strategy_research_max_book_skew_ms"])
            ):
                continue
            exit_fee = taker_fee(shares, bid, fee_bps)
            total_fees = float(trade["fees"]) + exit_fee
            pnl = shares * bid - float(trade["stake"]) - total_fees
            diagnostics["time_exit"] = {
                "rule": "first fresh full-depth bid from 30s through 45s",
                "scheduled_seconds": exit_after,
                "actual_elapsed_seconds": elapsed,
                "exit_bid": bid,
                "visible_bid_size": visible_bid_size,
                "required_shares": shares,
                "book_age_ms": book_age_ms,
                "book_skew_ms": book_skew_ms,
                "exit_fee": exit_fee,
                "queue_position_modeled": False,
            }
            self.db.execute(
                """UPDATE trades SET status='TIMEOUT_EXIT', exit_price=?, fees=?,
                          pnl=?, closed_at=?, note=note || ?, diagnostics_json=?
                     WHERE id=? AND status='OPEN'""",
                (
                    bid,
                    total_fees,
                    pnl,
                    str(snapshot.get("timestamp") or utc_iso()),
                    "; 30s fresh full-depth paper exit",
                    json.dumps(diagnostics, sort_keys=True),
                    int(trade["id"]),
                ),
            )
        self.db.commit()

    def _futures_lead_regime_direction_control(
        self,
        cfg: dict[str, float | bool],
        *,
        before_trade_id: int | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cutoff_sql = " AND id < ?" if before_trade_id is not None else ""
        parameters: tuple[Any, ...] = (
            (int(before_trade_id),) if before_trade_id is not None else ()
        )
        history = [
            dict(row)
            for row in self.db.execute(
                """SELECT id, market_id, status, closed_at
                     FROM trades
                    WHERE strategy='R_FUTURES_LEAD'
                      AND status IN ('SETTLED_WIN','SETTLED_LOSS')"""
                + cutoff_sql
                + " ORDER BY id DESC LIMIT 3",
                parameters,
            ).fetchall()
        ]
        automatic_reverse = bool(
            len(history) == 3
            and all(str(row["status"]) == "SETTLED_LOSS" for row in history)
        )
        mode_code = int(
            float(cfg["strategy_r_futures_lead_regime_reverse_3l_direction_mode"])
        )
        configured_mode = {0: "AUTO", 1: "FORWARD", 2: "REVERSE"}[mode_code]
        effective_reverse = (
            automatic_reverse if configured_mode == "AUTO" else configured_mode == "REVERSE"
        )
        return (
            {
                "configuredMode": configured_mode,
                "configuredModeCode": mode_code,
                "automaticDirection": "REVERSE" if automatic_reverse else "FORWARD",
                "effectiveDirection": "REVERSE" if effective_reverse else "FORWARD",
                "manualOverride": configured_mode != "AUTO",
                "historyReady": len(history) == 3,
                "recentLeadResults": [str(row["status"]) for row in history],
                "recentLeadMarketIds": [int(row["market_id"]) for row in history],
                "rule": "AUTO reverses only when the previous three original Lead results are losses",
                "appliesToPaperAndLive": True,
            },
            history,
        )

    def _evaluate_research_forward(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        cfg: dict[str, float | bool],
        context: dict[str, Any],
        existing: set[str],
    ) -> list[dict[str, Any]]:
        """Open only full-depth, shared-cap, forward-paper research fills."""
        if context.get("market_data_integrity_ok", True) is not True:
            return []
        market_id = int(snapshot["market_id"])
        current = self._research_samples.append(market_id, snapshot)
        if current is None:
            return []
        event_key = str(
            context.get("signal_event_sequence")
            or context.get("received_wall_ns")
            or int(current["timestamp_ns"])
        )
        # Event-cumulative OFI is valid only for a genuine Prediction event
        # stream.  The current dual-token REST fallback is a periodic snapshot
        # and must not be counted or labelled as exchange book events.
        if (
            context.get("signal_event_type") == "prediction"
            and context.get("prediction_sampling_mode") == "event_stream"
        ):
            self._research_samples.append_event_ofi(
                market_id, current, event_key
            )
        seconds_left = float(current["seconds_left"])
        exposure = self._research_open_exposure()
        cap = float(cfg["strategy_research_shared_cap_usdt"])
        opened: list[dict[str, Any]] = []

        for strategy in RESEARCH_STRATEGIES:
            prefix = strategy.lower()
            if not bool(cfg[f"strategy_{prefix}_enabled"]) or strategy in existing:
                continue
            params = RESEARCH_PARAMETERS[strategy]
            is_shadow = strategy in SHADOW_RESEARCH_STRATEGIES
            horizon = float(params["horizon"])
            if strategy in FUTURES_LEAD_EXPERIMENT_STRATEGIES:
                entry_delay = float(params["entry_delay_seconds"])
                entry_upper = horizon - float(params["lag"])
                entry_lower = horizon - entry_delay
            else:
                entry_lower, entry_upper = horizon - 3.0, horizon
            if not entry_lower <= seconds_left <= entry_upper:
                continue
            lag = float(params.get("lag", 0.0))
            previous = None
            cumulative_event_ofi = None
            source_trade = None
            regime_history = None
            regime_direction_control = None
            observer_decision = None
            observer_auto_v6_decision = None
            calibration_decision = None
            source_strategy = None
            if strategy in {
                *CONTINUOUS_CALIBRATION_STRATEGIES,
                *FUTURES_LEAD_FILTER_STRATEGIES,
                "R_FUTURES_LEAD_REVERSE",
                "R_FUTURES_LEAD_REGIME_REVERSE_3L",
                *FUTURES_LEAD_OBSERVER_STRATEGIES,
                *OBSERVER_COMBINATION_STRATEGIES,
                *OBSERVER_AUTO_V6_STRATEGIES,
            }:
                source_strategy = (
                    CONTINUOUS_CALIBRATION_RULES[strategy]
                    if strategy in CONTINUOUS_CALIBRATION_STRATEGIES
                    else FUTURES_LEAD_FILTER_RULES[strategy]["source_strategy"]
                    if strategy in FUTURES_LEAD_FILTER_STRATEGIES
                    else OBSERVER_COMBINATION_STRATEGY_RULES[strategy][0]
                    if strategy in OBSERVER_COMBINATION_STRATEGIES
                    else OBSERVER_AUTO_V6_STRATEGY_RULES[strategy]
                    if strategy in OBSERVER_AUTO_V6_STRATEGIES
                    else "R_FUTURES_LEAD"
                )
                source_trade = self.db.execute(
                    """SELECT id, side, opened_at, entry_price, stake, shares,
                              fees, model_probability, diagnostics_json
                         FROM trades
                        WHERE market_id=? AND strategy=?
                        ORDER BY id ASC LIMIT 1""",
                    (market_id, source_strategy),
                ).fetchone()
                if source_trade is None:
                    continue
                try:
                    source_diagnostics = json.loads(
                        str(source_trade["diagnostics_json"] or "{}")
                    )
                    source_signal = float(source_diagnostics["signal"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if strategy in FUTURES_LEAD_FILTER_STRATEGIES:
                    signal = filtered_futures_lead_signal(
                        strategy,
                        source_side=str(source_trade["side"]),
                        source_signal=source_signal,
                        source_entry=float(source_trade["entry_price"]),
                    )
                    if signal is None:
                        continue
                elif strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                    source_shares = float(source_trade["shares"])
                    if source_shares <= 0:
                        continue
                    effective_cost = float(source_trade["entry_price"]) + (
                        float(source_trade["fees"]) / source_shares
                    )
                    calibration_history = (
                        self._research_continuous_calibration_history(
                            source_strategy,
                            before_market_id=market_id,
                            limit=int(params["history_window"]),
                        )
                    )
                    calibration_decision = continuous_calibration_decision(
                        strategy,
                        source_side=str(source_trade["side"]),
                        source_signal=source_signal,
                        source_probability=source_trade["model_probability"],
                        effective_cost=effective_cost,
                        history=calibration_history,
                    )
                    if calibration_decision["allowed"] is not True:
                        continue
                    signal = {
                        "side": str(source_trade["side"]),
                        "signal": float(calibration_decision["calibrated_edge"]),
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "model_probability": float(
                            calibration_decision["calibrated_probability"]
                        ),
                        "model_edge": float(
                            calibration_decision["calibrated_edge"]
                        ),
                    }
                elif strategy in OBSERVER_AUTO_V6_STRATEGIES:
                    observer_gates = context.get("m01o_observer_gates")
                    observer_gate = (
                        observer_gates.get("F1")
                        if isinstance(observer_gates, dict)
                        else None
                    )
                    auto_history = self._research_observer_auto_v6_history(
                        source_strategy,
                        before_market_id=market_id,
                    )
                    observer_auto_v6_decision = observer_v6_auto_decision(
                        auto_history,
                        observer_gate,
                        expected_market_id=market_id,
                    )
                    if observer_auto_v6_decision["allowed"] is not True:
                        continue
                    signal = {
                        "side": str(source_trade["side"]),
                        "signal": source_signal,
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                    }
                elif strategy in {
                    *FUTURES_LEAD_OBSERVER_STRATEGIES,
                    *OBSERVER_COMBINATION_STRATEGIES,
                }:
                    observer_gates = context.get("m01o_observer_gates")
                    observer_gate = (
                        observer_gates.get("F1")
                        if isinstance(observer_gates, dict)
                        else None
                    )
                    observer_decision = futures_lead_observer_decision(
                        (
                            FUTURES_LEAD_OBSERVER_STRATEGY_VERSION[strategy]
                            if strategy in FUTURES_LEAD_OBSERVER_STRATEGIES
                            else OBSERVER_COMBINATION_STRATEGY_RULES[strategy][1]
                        ),
                        observer_gate,
                        expected_market_id=market_id,
                    )
                    if observer_decision["allowed"] is not True:
                        continue
                    signal = {
                        "side": str(source_trade["side"]),
                        "signal": source_signal,
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                    }
                elif strategy == "R_FUTURES_LEAD_REGIME_REVERSE_3L":
                    (
                        regime_direction_control,
                        regime_history,
                    ) = self._futures_lead_regime_direction_control(
                        cfg,
                        before_trade_id=int(source_trade["id"]),
                    )
                    signal = research_regime_futures_lead_signal(
                        str(source_trade["side"]),
                        source_signal,
                        reverse_after_three_losses=(
                            regime_direction_control["effectiveDirection"]
                            == "REVERSE"
                        ),
                    )
                else:
                    signal = research_reverse_futures_lead_signal(
                        str(source_trade["side"]), source_signal
                    )
            elif strategy in {"R_OFI_EVENT_CUM", "R_OFI_EVENT_CUM_FILTERED"}:
                cumulative_event_ofi = self._research_samples.cumulative_event_ofi(
                    market_id,
                    current,
                    float(params["window"]),
                    int(params["min_events"]),
                )
                signal = research_signal_for_strategy(
                    strategy,
                    current,
                    previous,
                    fee_bps=fee_bps,
                    slippage_bps=float(cfg["strategy_research_slippage_bps"]),
                    cumulative_event_ofi=cumulative_event_ofi,
                )
            elif strategy in FUTURES_LEAD_EXPERIMENT_STRATEGIES:
                previous = self._research_samples.lagged(market_id, current, lag)
                confirmation_previous = (
                    self._research_samples.lagged(market_id, previous, lag)
                    if previous is not None
                    else None
                )
                volatility = self._research_samples.realized_volatility_per_sqrt_second(
                    market_id,
                    current,
                    float(params["volatility_lookback_seconds"]),
                    int(params["min_volatility_observations"]),
                    float(params["min_volatility_span_seconds"]),
                    int(params["min_price_changes"]),
                )
                signal = research_signal_for_strategy(
                    strategy,
                    current,
                    previous,
                    fee_bps=fee_bps,
                    slippage_bps=float(cfg["strategy_research_slippage_bps"]),
                    confirmation_previous=confirmation_previous,
                    volatility=volatility,
                )
            else:
                if lag:
                    previous = self._research_samples.lagged(market_id, current, lag)
                signal = research_signal_for_strategy(
                    strategy,
                    current,
                    previous,
                    fee_bps=fee_bps,
                    slippage_bps=float(cfg["strategy_research_slippage_bps"]),
                    cumulative_event_ofi=cumulative_event_ofi,
                )
            if signal is None:
                continue
            stake = float(cfg[f"strategy_{prefix}_stake"])
            if not is_shadow and exposure + stake > cap + 1e-12:
                continue
            side = str(signal["side"])
            side_key = side.lower()
            visible_size = float(current[f"{side_key}_ask_size"])
            reserved_shares = (
                0.0 if is_shadow else self._research_reserved_shares(market_id, side)
            )
            available_size = max(0.0, visible_size - reserved_shares)
            execution_sample = {
                **current,
                f"{side_key}_ask_size": available_size,
            }
            candidate = research_execution_candidate(
                strategy,
                signal,
                execution_sample,
                snapshot,
                stake=stake,
                minimum_stake=float(cfg["strategy_research_min_stake_usdt"]),
                slippage_bps=float(cfg["strategy_research_slippage_bps"]),
                max_spread=float(cfg["strategy_research_max_spread"]),
                min_entry=float(cfg["strategy_research_min_entry"]),
                max_book_age_ms=min(
                    float(cfg["strategy_research_max_book_age_ms"]),
                    float(params.get("max_prediction_age_ms", math.inf)),
                ),
                max_book_skew_ms=float(cfg["strategy_research_max_book_skew_ms"]),
            )
            if candidate is None:
                continue
            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                actual_effective_cost = float(candidate["entry"]) + taker_fee(
                    1.0, float(candidate["entry"]), fee_bps
                )
                actual_edge = float(candidate["model_probability"]) - actual_effective_cost
                if actual_edge < float(params["min_edge"]):
                    continue
                candidate["model_edge"] = actual_edge
                calibration_decision["actual_effective_cost"] = actual_effective_cost
                calibration_decision["actual_calibrated_edge"] = actual_edge
            sample_index = None
            sample_segment = None
            if strategy in {
                *CONTINUOUS_CALIBRATION_STRATEGIES,
                *FUTURES_LEAD_FILTER_STRATEGIES,
                *FUTURES_LEAD_EXPERIMENT_STRATEGIES,
                *FUTURES_LEAD_OBSERVER_STRATEGIES,
                *OBSERVER_COMBINATION_STRATEGIES,
                *OBSERVER_AUTO_V6_STRATEGIES,
            }:
                sample_index, sample_segment = self._research_experiment_next_sample(
                    strategy
                )
            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "execution_model": "actual_top_ask_full_depth_with_slippage_v1",
                "shadow_only": is_shadow,
                "capital_model": (
                    "isolated_counterfactual_shadow_not_shared_cap"
                    if is_shadow
                    else "five_research_strategies_shared_open_exposure_cap"
                ),
                "shared_cap_usdt": cap,
                "open_exposure_before_usdt": exposure,
                "open_exposure_after_usdt": exposure + stake,
                "minimum_stake_usdt": float(cfg["strategy_research_min_stake_usdt"]),
                "requested_stake": stake,
                "filled_stake": stake,
                "fill_ratio": 1.0,
                "partial_fill": False,
                "raw_top_ask": candidate["raw_ask"],
                "simulated_entry_after_slippage": candidate["entry"],
                "raw_visible_ask_size": visible_size,
                "reserved_research_shares_before": reserved_shares,
                "available_ask_size_after_reservations": available_size,
                "visible_ask_size": candidate["visible_size"],
                "visible_notional_at_simulated_entry": candidate[
                    "visible_notional_at_simulated_entry"
                ],
                "spread": candidate["raw_ask"] - candidate["bid"],
                "book_age_ms": candidate["book_age_ms"],
                "book_skew_ms": candidate["book_skew_ms"],
                "signal": candidate["signal"],
                "signal_horizon_seconds": horizon,
                "signal_lag_seconds": lag,
                "selected_backtest_parameters": params,
                "fee_bps": fee_bps,
                "slippage_bps": float(cfg["strategy_research_slippage_bps"]),
                "queue_position_modeled": False,
                "full_top_of_book_depth_required": True,
                "signal_timestamp": snapshot.get("timestamp"),
                "realtime_context": json.loads(json.dumps(context, default=str)),
            }
            if sample_index is not None:
                diagnostics["chronological_sample_index"] = sample_index
                diagnostics["chronological_segment"] = sample_segment
                diagnostics["thresholds_frozen_before_collection"] = True
            for key in (
                "signed_residual_bps",
                "confirmation_first",
                "confirmation_second",
                "confirmation_windows",
                "same_direction_required",
                "model_probability",
                "up_probability",
                "model_edge",
                "model_sigma",
                "distance_to_strike_bps",
                "remaining_sigma_bps",
                "distance_z_score",
                "volatility_observation_count",
                "volatility_price_change_count",
                "volatility_span_seconds",
            ):
                if candidate.get(key) is not None:
                    diagnostics[key] = candidate[key]
            if candidate.get("votes") is not None:
                diagnostics["consensus_inputs"] = candidate["votes"]
            if candidate.get("event_ofi_count") is not None:
                diagnostics["event_ofi_count"] = candidate["event_ofi_count"]
                diagnostics["event_ofi_window_seconds"] = params["window"]
            if candidate.get("direction_reversed") is True:
                diagnostics["direction_reversed"] = True
                diagnostics["source_strategy"] = candidate.get("source_strategy")
                diagnostics["source_side"] = candidate.get("source_side")
                diagnostics["source_signal"] = candidate.get("source_signal")
                diagnostics["source_trade_id"] = int(source_trade["id"])
                diagnostics["source_trade_opened_at"] = source_trade["opened_at"]
                diagnostics["dependency_rule"] = (
                    "open_only_after_R_FUTURES_LEAD_trade"
                )
            if strategy == "R_FUTURES_LEAD_REGIME_REVERSE_3L":
                diagnostics["regime_rule"] = (
                    "auto_reverse_after_3_original_lead_losses_with_manual_override"
                )
                diagnostics["regime_reversed"] = bool(
                    candidate.get("regime_reversed")
                )
                diagnostics["recent_lead_results"] = [
                    str(row["status"]) for row in (regime_history or [])
                ]
                diagnostics["recent_lead_market_ids"] = [
                    int(row["market_id"]) for row in (regime_history or [])
                ]
                diagnostics["history_ready"] = len(regime_history or []) == 3
                diagnostics["history_uses_original_lead_counterfactual"] = True
                diagnostics["direction_mode"] = regime_direction_control[
                    "configuredMode"
                ]
                diagnostics["automatic_direction"] = regime_direction_control[
                    "automaticDirection"
                ]
                diagnostics["effective_direction"] = regime_direction_control[
                    "effectiveDirection"
                ]
                diagnostics["manual_direction_override"] = regime_direction_control[
                    "manualOverride"
                ]
            if strategy in FUTURES_LEAD_OBSERVER_STRATEGIES:
                diagnostics.update(
                    {
                        "observer_version": FUTURES_LEAD_OBSERVER_STRATEGY_VERSION[
                            strategy
                        ],
                        "observer_decision": observer_decision,
                        "source_strategy": "R_FUTURES_LEAD",
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "source_trade_id": int(source_trade["id"]),
                        "source_trade_opened_at": source_trade["opened_at"],
                        "dependency_rule": (
                            "open_only_after_same_market_R_FUTURES_LEAD_trade_"
                            "and_frozen_observer_rule_allows"
                        ),
                        "direction_reversed": False,
                    }
                )
            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                diagnostics.update(
                    {
                        "continuous_calibration": calibration_decision,
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "source_trade_id": int(source_trade["id"]),
                        "source_trade_opened_at": source_trade["opened_at"],
                        "dependency_rule": (
                            f"open_only_after_same_market_{source_strategy}_trade_"
                            "and_causal_rolling_calibration_allows"
                        ),
                        "direction_reversed": False,
                        "official_history_only": True,
                        "current_market_excluded_from_history": True,
                    }
                )
            if strategy in FUTURES_LEAD_FILTER_STRATEGIES:
                diagnostics.update(
                    {
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "source_entry": float(source_trade["entry_price"]),
                        "source_trade_id": int(source_trade["id"]),
                        "source_trade_opened_at": source_trade["opened_at"],
                        "filter_rule": signal["filter_rule"],
                        "dependency_rule": (
                            f"open_only_after_same_market_{source_strategy}_trade_"
                            "and_frozen_filter_allows"
                        ),
                        "direction_reversed": False,
                    }
                )
            if strategy in OBSERVER_COMBINATION_STRATEGIES:
                diagnostics.update(
                    {
                        "observer_version": OBSERVER_COMBINATION_STRATEGY_RULES[
                            strategy
                        ][1],
                        "observer_decision": observer_decision,
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "source_trade_id": int(source_trade["id"]),
                        "source_trade_opened_at": source_trade["opened_at"],
                        "dependency_rule": (
                            f"open_only_after_same_market_{source_strategy}_trade_"
                            "and_frozen_observer_rule_allows"
                        ),
                        "direction_reversed": False,
                    }
                )
            if strategy in OBSERVER_AUTO_V6_STRATEGIES:
                diagnostics.update(
                    {
                        "observer_version": "AUTO_V6",
                        "observer_auto_v6": observer_auto_v6_decision,
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_signal": source_signal,
                        "source_trade_id": int(source_trade["id"]),
                        "source_trade_opened_at": source_trade["opened_at"],
                        "dependency_rule": (
                            f"open_only_after_same_market_{source_strategy}_trade_"
                            "and_causal_official_auto_v6_switch_allows"
                        ),
                        "direction_reversed": False,
                        "official_history_only": True,
                        "current_market_excluded_from_history": True,
                    }
                )
            self.open_trade(
                strategy=strategy,
                topic_id=int(snapshot["topic_id"]),
                market_id=market_id,
                side=str(candidate["side"]),
                entry=float(candidate["entry"]),
                target=None,
                stake=stake,
                fee_rate_bps=fee_bps,
                strategy_version=(
                    f"{strategy}_shadow_paper_v2"
                    if strategy in {
                        *CONTINUOUS_CALIBRATION_STRATEGIES,
                        *FUTURES_LEAD_FILTER_STRATEGIES,
                        "R_FUTURES_LEAD_REVERSE",
                        "R_FUTURES_LEAD_REGIME_REVERSE_3L",
                    }
                    else f"{strategy}_shadow_paper_v1"
                    if is_shadow
                    else f"{strategy}_forward_paper_v1"
                ),
                model_probability=candidate.get("model_probability"),
                model_edge=candidate.get("model_edge"),
                model_sigma=candidate.get("model_sigma"),
                diagnostics=diagnostics,
                note=(
                    f"{strategy} {'isolated shadow' if is_shadow else 'forward paper'}; "
                    f"full first-level depth; "
                    + (
                        "not charged to primary shared cap"
                        if is_shadow
                        else f"shared exposure {exposure + stake:.2f}/{cap:.2f} USDT"
                    )
                ),
            )
            if not is_shadow:
                exposure += stake
            existing.add(strategy)
            opened_candidate = {
                    "strategy": strategy,
                    "topic_id": int(snapshot["topic_id"]),
                    "market_id": market_id,
                    "side": str(candidate["side"]),
                    "entry_price": float(candidate["entry"]),
                    "raw_top_ask": float(candidate["raw_ask"]),
                    "stake": stake,
                    "seconds_left": seconds_left,
                    "book_age_ms": snapshot.get("book_age_ms"),
                    "fee_bps": int(fee_bps),
                    "signal_timestamp": str(snapshot.get("timestamp") or utc_iso()),
                    "paper_only": True,
                    "live_orders_affected": False,
                    "market_data_integrity_ok": True,
                    "research_signal": float(candidate["signal"]),
                    "model_edge": candidate.get("model_edge"),
                    "event_ofi_count": candidate.get("event_ofi_count"),
                }
            if strategy in CONTINUOUS_CALIBRATION_STRATEGIES:
                opened_candidate.update(
                    {
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_trade_id": int(source_trade["id"]),
                        "continuous_calibration": calibration_decision,
                    }
                )
            elif strategy in FUTURES_LEAD_FILTER_STRATEGIES:
                opened_candidate.update(
                    {
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_trade_id": int(source_trade["id"]),
                        "source_entry_price": float(source_trade["entry_price"]),
                        "filter_rule": signal["filter_rule"],
                    }
                )
            elif strategy == "R_FUTURES_LEAD_REVERSE":
                opened_candidate.update(
                    {
                        "dependent_live_pair": True,
                        "source_strategy": "R_FUTURES_LEAD",
                        "source_side": str(candidate.get("source_side") or ""),
                        "source_trade_id": int(source_trade["id"]),
                    }
                )
            elif strategy == "R_FUTURES_LEAD_REGIME_REVERSE_3L":
                opened_candidate.update(
                    {
                        "source_strategy": "R_FUTURES_LEAD",
                        "source_side": str(candidate.get("source_side") or ""),
                        "source_trade_id": int(source_trade["id"]),
                        "regime_reversed": bool(
                            candidate.get("regime_reversed")
                        ),
                        "recent_lead_results": [
                            str(row["status"]) for row in (regime_history or [])
                        ],
                        "direction_mode": regime_direction_control[
                            "configuredMode"
                        ],
                        "automatic_direction": regime_direction_control[
                            "automaticDirection"
                        ],
                        "effective_direction": regime_direction_control[
                            "effectiveDirection"
                        ],
                        "manual_direction_override": regime_direction_control[
                            "manualOverride"
                        ],
                    }
                )
            elif strategy in FUTURES_LEAD_OBSERVER_STRATEGIES:
                opened_candidate.update(
                    {
                        "source_strategy": "R_FUTURES_LEAD",
                        "source_side": str(source_trade["side"]),
                        "source_trade_id": int(source_trade["id"]),
                        "observer_version": FUTURES_LEAD_OBSERVER_STRATEGY_VERSION[
                            strategy
                        ],
                        "observer_decision": observer_decision,
                    }
                )
            elif strategy in OBSERVER_COMBINATION_STRATEGIES:
                opened_candidate.update(
                    {
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_trade_id": int(source_trade["id"]),
                        "observer_version": OBSERVER_COMBINATION_STRATEGY_RULES[
                            strategy
                        ][1],
                        "observer_decision": observer_decision,
                    }
                )
            elif strategy in OBSERVER_AUTO_V6_STRATEGIES:
                opened_candidate.update(
                    {
                        "source_strategy": source_strategy,
                        "source_side": str(source_trade["side"]),
                        "source_trade_id": int(source_trade["id"]),
                        "observer_version": "AUTO_V6",
                        "observer_auto_v6": observer_auto_v6_decision,
                    }
                )
            opened.append(opened_candidate)
        return opened

    def maybe_enter_m_series(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate only M-family controls; safe for REST fallback or realtime events."""
        opened_candidates: list[dict[str, Any]] = []
        decision_started_wall_ns = time.time_ns()
        decision_started_monotonic_ns = time.monotonic_ns()
        context = dict(realtime_context or {})
        for key in (
            "received_wall_ns",
            "received_monotonic_ns",
            "signal_event_sequence",
            "trigger_source",
        ):
            if key not in context and snapshot.get(key) is not None:
                context[key] = snapshot[key]
        rest_fallback = realtime_context is None
        event_type = "rest" if rest_fallback else self._m_event_type(context)
        execution_eligible = bool(
            rest_fallback
            or (
                event_type == "prediction"
                and context.get("execution_eligible") is True
            )
        )
        can_update_spot_signal = rest_fallback or event_type == "spot"
        can_update_futures_signal = rest_fallback or event_type == "futures"
        market_id = int(snapshot["market_id"])
        cfg = self.config()
        try:
            elapsed_now = 300.0 - float(snapshot["seconds_left"])
        except (KeyError, TypeError, ValueError):
            return opened_candidates
        research_enabled = {
            strategy
            for strategy in RESEARCH_STRATEGIES
            if bool(cfg[f"strategy_{strategy.lower()}_enabled"])
        }
        research_window_active = bool(
            event_type == "prediction"
            and research_sampling_active(float(snapshot["seconds_left"]), research_enabled)
        )
        research_exit_active = bool(
            event_type == "prediction"
            and any(
                strategy in research_enabled
                and float(RESEARCH_PARAMETERS[strategy]["horizon"])
                - float(RESEARCH_PARAMETERS[strategy]["entry_delay_seconds"])
                - float(RESEARCH_PARAMETERS[strategy]["exit_after_seconds"])
                - float(RESEARCH_PARAMETERS[strategy]["exit_grace_seconds"])
                <= float(snapshot["seconds_left"])
                <= float(RESEARCH_PARAMETERS[strategy]["horizon"])
                - float(RESEARCH_PARAMETERS[strategy]["lag"])
                - float(RESEARCH_PARAMETERS[strategy]["exit_after_seconds"])
                for strategy in FUTURES_LEAD_EXIT_STRATEGIES
            )
        )
        standard_latest_execution = max(
            *(
                float(cfg[f"strategy_m{suffix}_entry_window_seconds"])
                for suffix in ("", "0", "1", "2", "3", "4", "5", "6")
            ),
            max(M7_DELAYS.values())
            + float(cfg["strategy_m7_execution_grace_seconds"]),
            float(cfg["strategy_m0w_entry_window_seconds"]),
            M6_BASIS_CAPTURE_WINDOW_SECONDS,
        )
        latest_execution = max(
            standard_latest_execution,
            float(cfg["strategy_m01_entry_window_seconds"]),
            300.0 - float(cfg["strategy_m01t180_min_seconds_left"]),
            float(cfg["strategy_m01o_entry_window_seconds"]),
            float(cfg["strategy_m01f_entry_window_seconds"]),
            float(cfg["strategy_m01r_entry_window_seconds"]),
            float(cfg["strategy_m01w_entry_window_seconds"]),
            *(
                300.0
                - float(RESEARCH_PARAMETERS[strategy]["horizon"])
                + float(RESEARCH_PARAMETERS[strategy].get("entry_delay_seconds", 3.0))
                + float(RESEARCH_PARAMETERS[strategy].get("exit_after_seconds", 0.0))
                + float(RESEARCH_PARAMETERS[strategy].get("exit_grace_seconds", 0.0))
                for strategy in research_enabled
            ),
        )
        if not math.isfinite(elapsed_now) or elapsed_now < 0 or elapsed_now > latest_execution:
            return opened_candidates
        if elapsed_now > standard_latest_execution:
            if event_type != "prediction":
                return opened_candidates
            cached = self._m_existing_trade_cache.get(market_id, set())
            m01_pending = cfg["strategy_m01_enabled"] and "M01" not in cached
            m01t180_pending = (
                cfg["strategy_m01t180_enabled"]
                and "M01T180" not in cached
                and float(snapshot["seconds_left"])
                > float(cfg["strategy_m01t180_min_seconds_left"])
            )
            m01t180d_pending = (
                cfg["strategy_m01t180d_enabled"]
                and "M01T180D" not in cached
                and float(snapshot["seconds_left"]) > 180.0
            )
            m01tasym_pending = (
                cfg["strategy_m01tasym_enabled"]
                and "M01TASYM" not in cached
                and float(snapshot["seconds_left"]) > 180.0
            )
            m01o_pending = cfg["strategy_m01o_enabled"] and "M01O" not in cached
            m01o_f1_pending = (
                cfg["strategy_m01o_f1_enabled"] and "M01O_F1" not in cached
            )
            m01o_live_pending = (
                cfg["strategy_m01o_live_enabled"]
                and "M01O_LIVE" not in cached
            )
            m01f_pending = cfg["strategy_m01f_enabled"] and "M01F" not in cached
            m01r_pending = cfg["strategy_m01r_enabled"] and "M01R" not in cached
            m01w_pending = cfg["strategy_m01w_enabled"] and "M01W" not in cached
            if not (
                m01_pending
                or m01t180_pending
                or m01t180d_pending
                or m01tasym_pending
                or m01o_pending
                or m01o_f1_pending
                or m01o_live_pending
                or m01f_pending
                or m01r_pending
                or m01w_pending
                or research_window_active
                or research_exit_active
            ):
                return opened_candidates
        with self.lock:
            changes_before = self.db.total_changes
            if not rest_fallback and event_type == "spot":
                # Canonical M6 basis collection is intentionally independent of
                # every strategy's enabled flag and of whether a trade can open.
                self._record_m_basis_sample(snapshot, context)
            if market_id not in self._m_existing_trade_cache:
                tracked_strategies = (*M_SERIES_STRATEGIES, *RESEARCH_STRATEGIES)
                placeholders = ",".join("?" for _ in tracked_strategies)
                self._m_existing_trade_cache[market_id] = {
                    str(row["strategy"])
                    for row in self.db.execute(
                        f"SELECT DISTINCT strategy FROM trades "
                        f"WHERE market_id=? AND strategy IN ({placeholders})",
                        (market_id, *tracked_strategies),
                    ).fetchall()
                }
            existing = self._m_existing_trade_cache[market_id]
            if research_exit_active:
                self._evaluate_research_time_exits(
                    snapshot, fee_bps, cfg, context
                )
            if research_window_active:
                opened_candidates.extend(
                    self._evaluate_research_forward(
                        snapshot, fee_bps, cfg, context, existing
                    )
                )

            def open_candidate(
                strategy: str,
                version: str,
                prefix: str,
                candidate: dict[str, Any] | None,
                source: str,
                rule: str,
                extra: dict[str, Any] | None = None,
            ) -> bool:
                if not execution_eligible or candidate is None or strategy in existing:
                    return False
                if self._open_m_series_trade(
                    strategy=strategy,
                    version=version,
                    snapshot=snapshot,
                    fee_bps=fee_bps,
                    cfg=cfg,
                    config_prefix=prefix,
                    candidate=candidate,
                    signal_source=source,
                    direction_rule=rule,
                    realtime_context=context,
                    decision_started_wall_ns=decision_started_wall_ns,
                    decision_started_monotonic_ns=decision_started_monotonic_ns,
                    extra_diagnostics=extra,
                ):
                    existing.add(strategy)
                    opened_candidate = {
                        "strategy": strategy,
                        "topic_id": int(snapshot["topic_id"]),
                        "market_id": market_id,
                        "side": str(candidate["side"]),
                        "entry_price": float(candidate["entry"]),
                        "seconds_left": float(snapshot["seconds_left"]),
                        "book_age_ms": snapshot.get("book_age_ms"),
                        "fee_bps": int(fee_bps),
                        "signal_timestamp": str(
                            snapshot.get("timestamp") or utc_iso()
                        ),
                        "signal_received_wall_ns": context.get(
                            "signal_received_wall_ns"
                        ),
                        "signal_received_monotonic_ns": context.get(
                            "signal_received_monotonic_ns"
                        ),
                        "market_data_integrity_ok": context.get(
                            "market_data_integrity_ok", True
                        ),
                    }
                    if strategy in {"M0W", "M01W"}:
                        gate_source = extra or {}
                        opened_candidate["m0w_gate"] = {
                            "version": "M0W_GATE_V2_ADJACENT_OFFICIAL_WIN",
                            "previous_market_id": gate_source.get(
                                "previous_m0_market_id"
                            ),
                            "previous_m0_status": gate_source.get(
                                "previous_m0_status"
                            ),
                            "previous_m0_pnl": gate_source.get("previous_m0_pnl"),
                            "previous_settlement_status": gate_source.get(
                                "previous_settlement_status"
                            ),
                            "previous_market_is_adjacent": gate_source.get(
                                "previous_market_is_adjacent"
                            ),
                            "current_market_start_ms": gate_source.get(
                                "current_market_start_ms"
                            ),
                            "previous_market_start_ms": gate_source.get(
                                "previous_market_start_ms"
                            ),
                            "previous_market_end_ms": gate_source.get(
                                "previous_market_end_ms"
                            ),
                        }
                    if extra and extra.get("maximum_entry_price") is not None:
                        opened_candidate["maximum_entry_price"] = float(
                            extra["maximum_entry_price"]
                        )
                    if extra and extra.get("paper_only") is True:
                        opened_candidate["paper_only"] = True
                    if extra and isinstance(
                        extra.get("market_observer_gate"), dict
                    ):
                        opened_candidate["market_observer_gate"] = dict(
                            extra["market_observer_gate"]
                        )
                    opened_candidates.append(opened_candidate)
                    return True
                return False

            base_signal_cache: dict[str, dict[str, Any] | None] = {}

            def signal_for(prefix: str, **kwargs: Any) -> dict[str, Any] | None:
                cache_key = prefix + json.dumps(kwargs, sort_keys=True)
                if cache_key not in base_signal_cache:
                    base_signal_cache[cache_key] = self._m_signal_context(
                        snapshot, cfg, prefix=prefix, **kwargs
                    )
                return base_signal_cache[cache_key]

            def control_signal(
                prefix: str,
                side: str,
                *,
                entry_window_seconds: float | None = None,
            ) -> dict[str, Any] | None:
                """Build a direction-only control without requiring Spot data."""
                try:
                    seconds_left = float(snapshot["seconds_left"])
                    start_price = float(snapshot["start_price"])
                except (KeyError, TypeError, ValueError):
                    return None
                elapsed = 300.0 - seconds_left
                if not (
                    math.isfinite(seconds_left)
                    and 0 <= elapsed
                    <= (
                        float(entry_window_seconds)
                        if entry_window_seconds is not None
                        else float(cfg[f"strategy_{prefix}_entry_window_seconds"])
                    )
                    and math.isfinite(start_price)
                    and start_price > 0
                ):
                    return None
                return {
                    "seconds_left": seconds_left,
                    "elapsed": elapsed,
                    "start_price": start_price,
                    "signal_price": None,
                    "signal_price_field": "none_control",
                    "signal_delta": None,
                    "signal_delta_bps": None,
                    "side": side,
                }

            def with_execution(
                prefix: str,
                signal: dict[str, Any] | None,
                side: str | None = None,
            ) -> dict[str, Any] | None:
                chosen = side or (str(signal["side"]) if signal else None)
                if signal is None or chosen not in {"UP", "DOWN"}:
                    return None
                execution = self._m_execution_context(
                    snapshot, cfg, prefix=prefix, side=chosen
                )
                return {**signal, **execution, "side": chosen} if execution else None

            def stored_signal(strategy: str) -> sqlite3.Row | None:
                return self._get_m_signal(strategy, market_id)

            def freeze_signal(
                strategy: str,
                signal: dict[str, Any] | None,
                signal_context: dict[str, Any] | None = None,
            ) -> sqlite3.Row | None:
                return self._store_m_signal(
                    strategy, snapshot, signal, signal_context or context
                )

            def execute_frozen(
                row: sqlite3.Row | None, prefix: str
            ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
                signal, signal_context = self._m_signal_row_data(row)
                if signal is None or row is None:
                    return None, signal_context
                if not self._m_book_is_after_signal(
                    row["signal_received_monotonic_ns"], snapshot, context
                ):
                    return None, signal_context
                return with_execution(prefix, signal), signal_context

            market_sequence = self._register_m_market_sequence(snapshot)
            previous_market_id = market_sequence["previous_market_id"]
            previous_is_adjacent = bool(
                previous_market_id is not None
                and market_sequence["previous_is_adjacent"]
            )
            previous_market = (
                self.db.execute(
                    """SELECT sequence.market_id, sequence.start_ms, sequence.end_ms,
                              settlements.status AS settlement_status,
                              m0.status AS m0_status, m0.pnl AS m0_pnl
                       FROM strategy_m_market_sequence AS sequence
                       LEFT JOIN market_settlements AS settlements
                         ON settlements.market_id=sequence.market_id
                       LEFT JOIN trades AS m0
                         ON m0.market_id=sequence.market_id AND m0.strategy='M0'
                       WHERE sequence.market_id=? LIMIT 1""",
                    (int(previous_market_id),),
                ).fetchone()
                if previous_market_id is not None
                else None
            )
            previous_m0_won = bool(
                previous_is_adjacent
                and previous_market is not None
                and previous_market["settlement_status"] == "OFFICIAL"
                and previous_market["m0_status"] == "SETTLED_WIN"
                and previous_market["m0_pnl"] is not None
                and float(previous_market["m0_pnl"]) > 0
            )
            previous_m0_diagnostics = {
                "required_previous_m0_result": "SETTLED_WIN",
                "required_previous_settlement_status": "OFFICIAL",
                "previous_market_is_adjacent": previous_is_adjacent,
                "current_market_start_ms": market_sequence["start_ms"],
                "previous_m0_market_id": (
                    int(previous_market["market_id"])
                    if previous_market is not None else None
                ),
                "previous_market_start_ms": (
                    int(previous_market["start_ms"])
                    if previous_market is not None
                    and previous_market["start_ms"] is not None else None
                ),
                "previous_market_end_ms": (
                    int(previous_market["end_ms"])
                    if previous_market is not None
                    and previous_market["end_ms"] is not None else None
                ),
                "previous_settlement_status": (
                    str(previous_market["settlement_status"])
                    if previous_market is not None
                    and previous_market["settlement_status"] is not None else None
                ),
                "previous_m0_status": (
                    str(previous_market["m0_status"])
                    if previous_market is not None
                    and previous_market["m0_status"] is not None else None
                ),
                "previous_m0_pnl": (
                    float(previous_market["m0_pnl"])
                    if previous_market is not None
                    and previous_market["m0_pnl"] is not None
                    else None
                ),
            }
            # M, M2-M6 freeze their first qualifying external-price signal.
            # A later Prediction event supplies the first post-signal executable book.
            if cfg["strategy_m_enabled"] and "M" not in existing:
                if rest_fallback:
                    candidate_m = self.strategy_m_entry_candidate(snapshot, cfg)
                    if candidate_m is not None:
                        candidate_m = {
                            **candidate_m,
                            "signal_price": candidate_m["spot_price"],
                            "signal_price_field": "spot_price",
                        }
                    signal_context_m: dict[str, Any] = {}
                else:
                    row_m = (
                        freeze_signal("M", signal_for("m"))
                        if can_update_spot_signal
                        else stored_signal("M")
                    )
                    candidate_m, signal_context_m = execute_frozen(row_m, "m")
                open_candidate(
                    "M",
                    (
                        "M_v1_opening_spot_direction_hold"
                        if rest_fallback
                        else "M_v2_ws_first_nonzero_post_signal_book_hold"
                    ),
                    "m", candidate_m,
                    "binance_spot_minus_official_start", "first_nonzero_spot_distance",
                    {
                        "signal_cross_source": True,
                        "signal_realtime_context": signal_context_m,
                    },
                )

            if cfg["strategy_m0_enabled"] and "M0" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                open_candidate(
                    "M0", "M0_v1_deterministic_hash_random_hold", "m0",
                    with_execution("m0", control_signal("m0", side), side),
                    "sha256_market_seed", "deterministic_unbiased_random",
                    {"random_seed": seed, "random_hash_prefix": digest.hex()[:16]},
                )

            if cfg["strategy_m01_enabled"] and "M01" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                signal_m01 = control_signal("m01", side)
                if rest_fallback:
                    candidate_m01 = with_execution("m01", signal_m01, side)
                    signal_context_m01: dict[str, Any] = {}
                else:
                    row_m01 = stored_signal("M01")
                    if row_m01 is None:
                        row_m01 = freeze_signal("M01", signal_m01)
                    candidate_m01, signal_context_m01 = execute_frozen(
                        row_m01, "m01"
                    )
                max_entry_m01 = float(cfg["strategy_m01_max_entry"])
                if (
                    candidate_m01 is not None
                    and float(candidate_m01["entry"]) > max_entry_m01
                ):
                    candidate_m01 = None
                open_candidate(
                    "M01",
                    "M01_v1_m0_direction_wait_030_hold",
                    "m01",
                    candidate_m01,
                    "sha256_market_seed",
                    "m0_direction_wait_selected_ask_at_or_below_limit",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "maximum_entry_price": max_entry_m01,
                        "signal_realtime_context": signal_context_m01,
                    },
                )

            if cfg["strategy_m01t180_enabled"] and "M01T180" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                min_seconds_left = float(
                    cfg["strategy_m01t180_min_seconds_left"]
                )
                entry_window_seconds = 300.0 - min_seconds_left
                signal_m01t180 = control_signal(
                    "m01t180",
                    side,
                    entry_window_seconds=entry_window_seconds,
                )
                # The comparison is intentionally strict: exactly 180 seconds
                # remaining belongs to the prohibited half of the experiment.
                if float(snapshot["seconds_left"]) <= min_seconds_left:
                    signal_m01t180 = None
                if rest_fallback:
                    candidate_m01t180 = with_execution(
                        "m01t180", signal_m01t180, side
                    )
                    signal_context_m01t180: dict[str, Any] = {}
                else:
                    row_m01t180 = stored_signal("M01T180")
                    if row_m01t180 is None:
                        row_m01t180 = freeze_signal("M01T180", signal_m01t180)
                    candidate_m01t180, signal_context_m01t180 = execute_frozen(
                        row_m01t180, "m01t180"
                    )
                if float(snapshot["seconds_left"]) <= min_seconds_left:
                    candidate_m01t180 = None
                max_entry_m01t180 = float(cfg["strategy_m01t180_max_entry"])
                if (
                    candidate_m01t180 is not None
                    and float(candidate_m01t180["entry"]) > max_entry_m01t180
                ):
                    candidate_m01t180 = None
                open_candidate(
                    "M01T180",
                    "M01T180_v1_m01_require_seconds_left_gt_180_hold",
                    "m01t180",
                    candidate_m01t180,
                    "sha256_market_seed",
                    "m01_direction_wait_selected_ask_at_or_below_limit_before_time_cutoff",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "minimum_seconds_left_exclusive": min_seconds_left,
                        "maximum_entry_price": max_entry_m01t180,
                        "control_strategy": "M01",
                        "signal_realtime_context": signal_context_m01t180,
                    },
                )

            if cfg["strategy_m01t180d_enabled"] and "M01T180D" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                min_seconds_left = 180.0
                signal_m01t180d = control_signal(
                    "m01t180",
                    side,
                    entry_window_seconds=300.0 - min_seconds_left,
                )
                if side != "DOWN" or float(snapshot["seconds_left"]) <= min_seconds_left:
                    signal_m01t180d = None
                if rest_fallback:
                    candidate_m01t180d = with_execution(
                        "m01t180", signal_m01t180d, side
                    )
                    signal_context_m01t180d: dict[str, Any] = {}
                else:
                    row_m01t180d = stored_signal("M01T180D")
                    if row_m01t180d is None:
                        row_m01t180d = freeze_signal("M01T180D", signal_m01t180d)
                    candidate_m01t180d, signal_context_m01t180d = execute_frozen(
                        row_m01t180d, "m01t180"
                    )
                if side != "DOWN" or float(snapshot["seconds_left"]) <= min_seconds_left:
                    candidate_m01t180d = None
                max_entry_m01t180d = float(cfg["strategy_m01t180_max_entry"])
                if (
                    candidate_m01t180d is not None
                    and float(candidate_m01t180d["entry"]) > max_entry_m01t180d
                ):
                    candidate_m01t180d = None
                open_candidate(
                    "M01T180D",
                    "M01T180D_v1_down_only_seconds_left_gt_180_hold",
                    "m01t180",
                    candidate_m01t180d,
                    "sha256_market_seed",
                    "m01_down_only_wait_selected_ask_at_or_below_limit_before_time_cutoff",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "required_side": "DOWN",
                        "minimum_seconds_left_exclusive": min_seconds_left,
                        "maximum_entry_price": max_entry_m01t180d,
                        "paper_only": True,
                        "control_strategy": "M01T180",
                        "signal_realtime_context": signal_context_m01t180d,
                    },
                )

            if cfg["strategy_m01tasym_enabled"] and "M01TASYM" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                min_seconds_left = 210.0 if side == "UP" else 180.0
                signal_m01tasym = control_signal(
                    "m01t180",
                    side,
                    entry_window_seconds=300.0 - min_seconds_left,
                )
                if float(snapshot["seconds_left"]) <= min_seconds_left:
                    signal_m01tasym = None
                if rest_fallback:
                    candidate_m01tasym = with_execution(
                        "m01t180", signal_m01tasym, side
                    )
                    signal_context_m01tasym: dict[str, Any] = {}
                else:
                    row_m01tasym = stored_signal("M01TASYM")
                    if row_m01tasym is None:
                        row_m01tasym = freeze_signal("M01TASYM", signal_m01tasym)
                    candidate_m01tasym, signal_context_m01tasym = execute_frozen(
                        row_m01tasym, "m01t180"
                    )
                if float(snapshot["seconds_left"]) <= min_seconds_left:
                    candidate_m01tasym = None
                max_entry_m01tasym = float(cfg["strategy_m01t180_max_entry"])
                if (
                    candidate_m01tasym is not None
                    and float(candidate_m01tasym["entry"]) > max_entry_m01tasym
                ):
                    candidate_m01tasym = None
                open_candidate(
                    "M01TASYM",
                    "M01TASYM_v1_up_gt_210_down_gt_180_hold",
                    "m01t180",
                    candidate_m01tasym,
                    "sha256_market_seed",
                    "m01_side_specific_time_cutoff_wait_selected_ask_at_or_below_limit",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "up_minimum_seconds_left_exclusive": 210.0,
                        "down_minimum_seconds_left_exclusive": 180.0,
                        "applied_minimum_seconds_left_exclusive": min_seconds_left,
                        "maximum_entry_price": max_entry_m01tasym,
                        "paper_only": True,
                        "control_strategy": "M01T180",
                        "signal_realtime_context": signal_context_m01tasym,
                    },
                )

            observer_gates_raw = context.get("m01o_observer_gates")
            observer_gates = (
                dict(observer_gates_raw)
                if isinstance(observer_gates_raw, dict)
                else {}
            )
            legacy_f2_gate = context.get("m01o_observer_gate")
            if "F2" not in observer_gates and isinstance(legacy_f2_gate, dict):
                observer_gates["F2"] = dict(legacy_f2_gate)
            filter_profiles = (
                (
                    "M01O",
                    "F2",
                    bool(cfg["strategy_m01o_enabled"]),
                    "M01O_F2_v2_shared_m01_candidate_strict_range_gate_hold",
                ),
                (
                    "M01O_F1",
                    "F1",
                    bool(cfg["strategy_m01o_f1_enabled"]),
                    "M01O_F1_v1_shared_m01_candidate_relaxed_gate_hold",
                ),
                (
                    "M01O_LIVE",
                    "LIVE",
                    bool(cfg["strategy_m01o_live_enabled"]),
                    "M01O_LIVE_v1_shared_m01_candidate_dual_touch_gate_hold",
                ),
            )
            seed = int(cfg["strategy_m0_seed"])
            digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
            side = "UP" if digest[0] < 128 else "DOWN"
            max_entry_m01o = float(cfg["strategy_m01o_max_entry"])
            for strategy, profile, enabled_profile, version in filter_profiles:
                if not enabled_profile or strategy in existing:
                    continue
                signal_m01o = control_signal("m01o", side)
                if rest_fallback:
                    candidate_m01o = with_execution(
                        "m01o", signal_m01o, side
                    )
                    signal_context_m01o: dict[str, Any] = {}
                else:
                    row_m01o = stored_signal(strategy)
                    if row_m01o is None:
                        row_m01o = freeze_signal(strategy, signal_m01o)
                    candidate_m01o, signal_context_m01o = execute_frozen(
                        row_m01o, "m01o"
                    )
                if (
                    candidate_m01o is None
                    or float(candidate_m01o["entry"]) > max_entry_m01o
                ):
                    continue
                observer_gate_raw = observer_gates.get(profile)
                observer_gate = (
                    dict(observer_gate_raw)
                    if isinstance(observer_gate_raw, dict)
                    else {
                        "allowed": False,
                        "status": "BLOCK",
                        "profile": profile,
                        "blockCategory": "MISSING_OR_STALE",
                        "reason": "市場狀況觀測器門檻資料不可用",
                        "paperOnly": True,
                        "liveOrdersAffected": False,
                    }
                )
                if (
                    strategy == "M01O_F1"
                    and float(snapshot["seconds_left"])
                    <= float(M01O_F1_MIN_SECONDS_LEFT)
                ):
                    observer_gate = {
                        **observer_gate,
                        "allowed": False,
                        "status": "BLOCK",
                        "blockCategory": "ENTRY_CUTOFF",
                        "reason": "F1 does not enter during the final 30 seconds",
                        "minimumSecondsLeftExclusive": float(
                            M01O_F1_MIN_SECONDS_LEFT
                        ),
                    }
                opened_profile = open_candidate(
                    strategy,
                    version,
                    "m01o",
                    (
                        candidate_m01o
                        if observer_gate.get("allowed") is True
                        else None
                    ),
                    "shared_sha256_market_seed_plus_market_observer",
                    f"shared_m01_candidate_then_{profile.lower()}_observer_gate",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "maximum_entry_price": max_entry_m01o,
                        "market_observer_gate": observer_gate,
                        "filter_profile": profile,
                        **(
                            {
                                "minimum_seconds_left_exclusive": float(
                                    M01O_F1_MIN_SECONDS_LEFT
                                )
                            }
                            if strategy == "M01O_F1"
                            else {}
                        ),
                        "paper_only": True,
                        "shared_candidate_family": "M01O_FILTER_V2",
                        "signal_realtime_context": signal_context_m01o,
                    },
                )
                self._record_m01o_gate_decision(
                    strategy=strategy,
                    profile=profile,
                    snapshot=snapshot,
                    candidate=candidate_m01o,
                    gate=observer_gate,
                    fee_bps=fee_bps,
                    opened=opened_profile,
                )

            if cfg["strategy_m01f_enabled"] and "M01F" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                signal_m01f = control_signal("m01f", side)
                if rest_fallback:
                    candidate_m01f = with_execution("m01f", signal_m01f, side)
                    signal_context_m01f: dict[str, Any] = {}
                else:
                    row_m01f = stored_signal("M01F")
                    if row_m01f is None:
                        row_m01f = freeze_signal("M01F", signal_m01f)
                    candidate_m01f, signal_context_m01f = execute_frozen(
                        row_m01f, "m01f"
                    )
                min_entry_m01f = float(cfg["strategy_m01f_min_entry"])
                max_entry_m01f = float(cfg["strategy_m01f_max_entry"])
                if candidate_m01f is not None and not (
                    min_entry_m01f
                    <= float(candidate_m01f["entry"])
                    <= max_entry_m01f
                ):
                    candidate_m01f = None
                open_candidate(
                    "M01F",
                    "M01F_v1_m0_direction_wait_020_030_hold",
                    "m01f",
                    candidate_m01f,
                    "sha256_market_seed",
                    "m0_direction_wait_selected_ask_inside_floor_band",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "minimum_entry_price": min_entry_m01f,
                        "maximum_entry_price": max_entry_m01f,
                        "signal_realtime_context": signal_context_m01f,
                    },
                )

            if cfg["strategy_m01r_enabled"] and "M01R" not in existing:
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                signal_m01r = control_signal("m01r", side)
                if rest_fallback:
                    raw_candidate_m01r = with_execution("m01r", signal_m01r, side)
                    signal_context_m01r: dict[str, Any] = {}
                else:
                    row_m01r = stored_signal("M01R")
                    if row_m01r is None:
                        row_m01r = freeze_signal("M01R", signal_m01r)
                    raw_candidate_m01r, signal_context_m01r = execute_frozen(
                        row_m01r, "m01r"
                    )
                if not execution_eligible:
                    raw_candidate_m01r = None
                candidate_m01r, rebound_diagnostics = (
                    self._strategy_m01r_rebound_candidate(
                        snapshot,
                        cfg,
                        raw_candidate_m01r,
                        context,
                    )
                )
                open_candidate(
                    "M01R",
                    "M01R_v1_m0_direction_low_water_plus_010_rebound_hold",
                    "m01r",
                    candidate_m01r,
                    "sha256_market_seed",
                    "m0_direction_buy_after_selected_ask_rebounds_from_low_water",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "maximum_anchor_price": float(
                            cfg["strategy_m01r_max_anchor"]
                        ),
                        "configured_rebound_amount": float(
                            cfg["strategy_m01r_rebound"]
                        ),
                        "signal_realtime_context": signal_context_m01r,
                        **rebound_diagnostics,
                    },
                )

            if (
                cfg["strategy_m0w_enabled"]
                and "M0W" not in existing
                and previous_m0_won
            ):
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                open_candidate(
                    "M0W",
                    "M0W_v2_adjacent_previous_m0_official_win_gate_hash_random_hold",
                    "m0w",
                    with_execution("m0w", control_signal("m0w", side), side),
                    "sha256_market_seed",
                    "enter_current_m0_direction_only_after_previous_m0_win",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        **previous_m0_diagnostics,
                    },
                )

            if (
                cfg["strategy_m01w_enabled"]
                and "M01W" not in existing
                and previous_m0_won
            ):
                seed = int(cfg["strategy_m0_seed"])
                digest = hashlib.sha256(f"M0:{seed}:{market_id}".encode()).digest()
                side = "UP" if digest[0] < 128 else "DOWN"
                signal_m01w = control_signal("m01w", side)
                if rest_fallback:
                    candidate_m01w = with_execution("m01w", signal_m01w, side)
                    signal_context_m01w: dict[str, Any] = {}
                else:
                    row_m01w = stored_signal("M01W")
                    if row_m01w is None:
                        row_m01w = freeze_signal("M01W", signal_m01w)
                    candidate_m01w, signal_context_m01w = execute_frozen(
                        row_m01w, "m01w"
                    )
                max_entry_m01w = float(cfg["strategy_m01w_max_entry"])
                if (
                    candidate_m01w is not None
                    and float(candidate_m01w["entry"]) > max_entry_m01w
                ):
                    candidate_m01w = None
                open_candidate(
                    "M01W",
                    "M01W_v3_adjacent_previous_m0_official_win_gate_wait_030_hold",
                    "m01w",
                    candidate_m01w,
                    "sha256_market_seed",
                    "previous_m0_win_then_wait_selected_ask_at_or_below_limit",
                    {
                        "random_seed": seed,
                        "random_hash_prefix": digest.hex()[:16],
                        "maximum_entry_price": max_entry_m01w,
                        "signal_realtime_context": signal_context_m01w,
                        **previous_m0_diagnostics,
                    },
                )

            if cfg["strategy_m1_enabled"] and "M1" not in existing:
                open_candidate(
                    "M1", "M1_v1_always_up_hold", "m1",
                    with_execution("m1", control_signal("m1", "UP"), "UP"),
                    "constant_control", "always_up",
                )

            if cfg["strategy_m2_enabled"] and "M2" not in existing:
                row_m2 = (
                    freeze_signal("M2", signal_for("m2"))
                    if can_update_spot_signal
                    else stored_signal("M2")
                )
                candidate_m2, signal_context_m2 = execute_frozen(row_m2, "m2")
                open_candidate(
                    "M2",
                    (
                        "M2_v1_opening_spot_direction_hold"
                        if rest_fallback
                        else "M2_v2_ws_first_nonzero_post_signal_book_hold"
                    ),
                    "m2",
                    candidate_m2,
                    "binance_spot_minus_official_start", "first_nonzero_spot_distance",
                    {"signal_realtime_context": signal_context_m2},
                )

            if cfg["strategy_m3_enabled"] and "M3" not in existing:
                threshold = float(cfg["strategy_m3_min_abs_delta_bps"])
                row_m3 = (
                    freeze_signal(
                        "M3", signal_for("m3", min_abs_delta_bps=threshold)
                    )
                    if can_update_spot_signal
                    else stored_signal("M3")
                )
                candidate_m3, signal_context_m3 = execute_frozen(row_m3, "m3")
                open_candidate(
                    "M3", "M3_v1_threshold_spot_direction_hold", "m3",
                    candidate_m3,
                    "binance_spot_minus_official_start", "minimum_absolute_spot_distance",
                    {
                        "minimum_absolute_distance_bps": threshold,
                        "signal_realtime_context": signal_context_m3,
                    },
                )

            if cfg["strategy_m4_enabled"] and "M4" not in existing:
                confirmed_m4 = (
                    self._strategy_m4_candidate(snapshot, cfg, context)
                    if can_update_spot_signal
                    and (
                        rest_fallback
                        or context.get("signal_event_sequence") is not None
                    )
                    else None
                )
                row_m4 = (
                    freeze_signal("M4", confirmed_m4)
                    if confirmed_m4 is not None
                    else stored_signal("M4")
                )
                candidate_m4, signal_context_m4 = execute_frozen(row_m4, "m4")
                open_candidate(
                    "M4", "M4_v1_consecutive_spot_confirmation_hold", "m4",
                    candidate_m4,
                    "binance_spot_minus_official_start", "consecutive_same_direction",
                    ({
                        "required_observations": int(
                            cfg["strategy_m4_required_observations"]
                        ),
                        "confirmation_count": candidate_m4["confirmation_count"],
                        "confirmation_observations": candidate_m4[
                            "confirmation_observations"
                        ],
                        "signal_realtime_context": signal_context_m4,
                    } if candidate_m4 else None),
                )

            if cfg["strategy_m5_enabled"] and "M5" not in existing:
                m5_context = {
                    **context,
                    "signal_exchange_event_ms": context.get(
                        "signal_exchange_event_ms", snapshot.get("futures_timestamp_ms")
                    ),
                }
                row_m5 = (
                    freeze_signal(
                        "M5",
                        signal_for("m5", price_field="futures_price"),
                        m5_context,
                    )
                    if can_update_futures_signal
                    else stored_signal("M5")
                )
                candidate_m5, signal_context_m5 = execute_frozen(row_m5, "m5")
                open_candidate(
                    "M5", "M5_v1_usdm_perpetual_last_trade_hold", "m5",
                    candidate_m5,
                    "binance_usdm_perpetual_agg_trade_minus_official_start",
                    "perpetual_last_trade_sign",
                    {
                        "futures_price": snapshot.get("futures_price"),
                        "futures_timestamp_ms": snapshot.get("futures_timestamp_ms"),
                        "futures_age_ms": snapshot.get("futures_age_ms"),
                        "futures_agg_trade_id": snapshot.get("futures_agg_trade_id"),
                        "signal_realtime_context": signal_context_m5,
                    },
                )

            if cfg["strategy_m6_enabled"] and "M6" not in existing:
                raw_signal = (
                    signal_for("m6", require_nonzero=False)
                    if can_update_spot_signal
                    else None
                )
                basis = (
                    self._strategy_m6_basis_context(market_id, cfg)
                    if can_update_spot_signal
                    else None
                )
                adjusted_signal = None
                if raw_signal is not None and basis is not None:
                    adjusted_bps = (
                        float(raw_signal["signal_delta_bps"])
                        - float(basis["basis_mean_bps"])
                    )
                    if adjusted_bps != 0:
                        side = "UP" if adjusted_bps > 0 else "DOWN"
                        adjusted_signal = {
                            **raw_signal,
                            "side": side,
                            "raw_signal_delta": raw_signal["signal_delta"],
                            "raw_signal_delta_bps": raw_signal["signal_delta_bps"],
                            "signal_delta": adjusted_bps
                            / 10_000.0
                            * float(raw_signal["start_price"]),
                            "signal_delta_bps": adjusted_bps,
                            "signal_price": float(raw_signal["start_price"])
                            * (1.0 + adjusted_bps / 10_000.0),
                            "signal_price_field": "basis_adjusted_spot_price",
                            "basis_context": basis,
                            "raw_spot_price": raw_signal["signal_price"],
                            "raw_distance": raw_signal["signal_delta"],
                            "raw_distance_bps": raw_signal["signal_delta_bps"],
                        }
                row_m6 = (
                    freeze_signal("M6", adjusted_signal)
                    if can_update_spot_signal
                    else stored_signal("M6")
                )
                candidate_m6, signal_context_m6 = execute_frozen(row_m6, "m6")
                frozen_basis = (
                    candidate_m6.get("basis_context") if candidate_m6 else None
                )
                open_candidate(
                    "M6", "M6_v3_causal_spot_start_basis_adjusted_hold", "m6",
                    candidate_m6,
                    "basis_adjusted_binance_spot_minus_official_start",
                    "causal_previous_market_opening_basis_adjustment",
                    ({
                        **frozen_basis,
                        "raw_spot_price": candidate_m6["raw_spot_price"],
                        "raw_distance": candidate_m6["raw_distance"],
                        "raw_distance_bps": candidate_m6["raw_distance_bps"],
                        "adjusted_distance_bps": candidate_m6[
                            "signal_delta_bps"
                        ],
                        "basis_scope": "previous_market_causal_basis_samples_only",
                        "no_lookahead": True,
                        "signal_realtime_context": signal_context_m6,
                    } if candidate_m6 and frozen_basis else None),
                )

            any_m7_enabled = any(
                bool(cfg[f"strategy_{strategy.lower()}_enabled"])
                for strategy in M7_DELAYS
            )
            grace = float(cfg["strategy_m7_execution_grace_seconds"])
            deadline_value = context.get("m7_deadline_seconds")
            if (
                any_m7_enabled
                and event_type == "scheduler"
                and deadline_value is not None
            ):
                deadline = float(deadline_value)
                strategy = next(
                    (
                        name
                        for name, configured_delay in M7_DELAYS.items()
                        if configured_delay == deadline
                    ),
                    None,
                )
                raw_m7 = signal_for("m7")
                if (
                    strategy is not None
                    and cfg[f"strategy_{strategy.lower()}_enabled"]
                    and strategy not in existing
                    and raw_m7 is not None
                ):
                    freeze_signal(
                        strategy,
                        {
                            **raw_m7,
                            "deadline_elapsed_seconds": deadline,
                            "signal_deadline_lag_seconds": 0.0,
                            "asof_spot_age_ms": context.get("m7_asof_spot_age_ms"),
                            "asof_spot_event_sequence": context.get(
                                "m7_asof_spot_event_sequence"
                            ),
                        },
                    )

            if any_m7_enabled:
                execution_elapsed = 300.0 - float(snapshot["seconds_left"])
                for strategy, delay in M7_DELAYS.items():
                    if (
                        not cfg[f"strategy_{strategy.lower()}_enabled"]
                        or strategy in existing
                        or execution_elapsed < delay
                        or execution_elapsed > delay + grace
                    ):
                        continue
                    signal_row = stored_signal(strategy)
                    signal_data, signal_context = self._m_signal_row_data(signal_row)
                    if signal_row is None or signal_data is None:
                        continue
                    if not self._m_book_is_after_signal(
                        signal_row["signal_received_monotonic_ns"], snapshot, context
                    ):
                        continue
                    execution = self._m_execution_context(
                        snapshot, cfg, prefix="m7", side=str(signal_row["side"])
                    )
                    candidate_m7 = (
                        {
                            **signal_data,
                            **execution,
                            "side": signal_row["side"],
                            "signal_timestamp": signal_row["signal_timestamp"],
                        }
                        if execution
                        else None
                    )
                    open_candidate(
                        strategy,
                        f"{strategy}_v2_market_open_deadline_ws_hold",
                        "m7",
                        candidate_m7,
                        "binance_spot_at_or_after_market_open_deadline",
                        "independent_market_open_delay",
                        ({
                            "requested_delay_seconds": delay,
                            "actual_delay_seconds": execution_elapsed,
                            "execution_lag_seconds": execution_elapsed - delay,
                            "signal_elapsed_seconds": signal_row["signal_elapsed"],
                            "signal_deadline_lag_seconds": float(
                                signal_data.get("signal_deadline_lag_seconds") or 0.0
                            ),
                            "deadline_reference": "market_open_monotonic_clock",
                            "signal_realtime_context": signal_context,
                        } if candidate_m7 else None),
                    )
            if self.db.total_changes != changes_before:
                self.db.commit()
        return opened_candidates

    @staticmethod
    def strategy_b2_entry_candidate(
        snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Return B2's time-scaled late-confidence entry, if one is available."""
        seconds_left = float(snapshot["seconds_left"])
        last_seconds = float(cfg["strategy_b2_last_seconds"])
        min_seconds_left = float(cfg["strategy_b2_min_seconds_left"])
        if not min_seconds_left <= seconds_left <= last_seconds:
            return None

        valid = [
            (side, float(snapshot[key]))
            for side, key in (("UP", "up_ask"), ("DOWN", "down_ask"))
            if snapshot.get(key) is not None
        ]
        if not valid:
            return None
        side, entry = max(valid, key=lambda item: item[1])
        if not (
            float(cfg["strategy_b2_min_price"])
            <= entry
            <= float(cfg["strategy_b2_max_price"])
        ):
            return None
        raw_bid = snapshot.get(f"{side.lower()}_bid")
        bid = float(raw_bid) if raw_bid is not None else None
        if (
            bid is None
            or not math.isfinite(bid)
            or not 0 <= bid <= entry
            or bid < float(cfg["strategy_b2_stop_loss_price"])
        ):
            # Never open a position that is already executable below its stop.
            return None

        progress = min(
            1.0,
            max(
                0.0,
                (last_seconds - seconds_left) / (last_seconds - min_seconds_left),
            ),
        )
        curve = float(cfg["strategy_b2_size_curve"])
        min_stake = float(cfg["strategy_b2_min_stake"])
        max_stake = float(cfg["strategy_b2_max_stake"])
        requested_stake = min_stake + (max_stake - min_stake) * progress**curve
        visible_size = snapshot.get(f"{side.lower()}_ask_size")
        fill = top_ask_fill(requested_stake, entry, visible_size)
        if fill is None:
            return None
        return {
            "side": side,
            "entry": entry,
            "bid": bid,
            "seconds_left": seconds_left,
            "progress": progress,
            "risk_level": 1.0 - progress,
            "curve": curve,
            "visible_ask_size": float(visible_size),
            **fill,
        }

    def momentum_candidate(
        self, snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> tuple[str, float, float, float, float] | None:
        """Return a same-source spot move confirmed by the prediction market."""
        seconds_left = float(snapshot["seconds_left"])
        if not (
            float(cfg["strategy_e_min_seconds_left"])
            <= seconds_left
            <= float(cfg["strategy_e_max_seconds_left"])
        ):
            return None

        with self.lock:
            first = self.db.execute(
                """SELECT spot_price, seconds_left FROM observations
                   WHERE market_id=? ORDER BY id ASC LIMIT 1""",
                (int(snapshot["market_id"]),),
            ).fetchone()
        if (
            first is None
            or float(first["seconds_left"])
            < float(cfg["strategy_e_min_baseline_seconds_left"])
            or float(first["spot_price"]) <= 0
        ):
            return None

        anchor = float(first["spot_price"])
        move_bps = (float(snapshot["spot_price"]) / anchor - 1.0) * 10_000
        if abs(move_bps) < float(cfg["strategy_e_min_spot_move_bps"]):
            return None
        side = "UP" if move_bps > 0 else "DOWN"
        opposite = "DOWN" if side == "UP" else "UP"
        entry = snapshot[f"{side.lower()}_ask"]
        bid = snapshot[f"{side.lower()}_bid"]
        opposite_ask = snapshot[f"{opposite.lower()}_ask"]
        if entry is None or bid is None or opposite_ask is None:
            return None
        entry = float(entry)
        spread = max(0.0, entry - float(bid))
        if not (
            float(cfg["strategy_e_min_entry"])
            <= entry
            <= float(cfg["strategy_e_max_entry"])
        ):
            return None
        if entry < float(opposite_ask) or spread > float(cfg["strategy_e_max_spread"]):
            return None
        visible_size = snapshot.get(f"{side.lower()}_ask_size")
        required_shares = float(cfg["strategy_e_stake"]) / entry
        if visible_size is not None and float(visible_size) < required_shares:
            return None
        return side, entry, move_bps, spread, float(first["seconds_left"])

    def spot_model_context(
        self,
        snapshot: dict[str, Any],
        *,
        lookback_observations: int,
        min_return_samples: int,
        sigma_floor: float,
        min_baseline_seconds_left: float,
        full_interval: bool = False,
    ) -> dict[str, float] | None:
        """Build the shared, same-source spot baseline and rolling volatility context."""
        market_id = int(snapshot["market_id"])
        with self.lock:
            first = self.db.execute(
                """SELECT timestamp, spot_price, seconds_left FROM observations
                   WHERE market_id=? ORDER BY id ASC LIMIT 1""",
                (market_id,),
            ).fetchone()
            if full_interval:
                recent = self.db.execute(
                    """SELECT timestamp, spot_price FROM observations
                       WHERE market_id=? ORDER BY id DESC LIMIT ?""",
                    (market_id, max(2, int(lookback_observations))),
                ).fetchall()[::-1]
            else:
                recent = self.db.execute(
                    """SELECT timestamp, spot_price FROM observations
                       WHERE market_id=? ORDER BY id DESC LIMIT ?""",
                    (market_id, max(2, int(lookback_observations))),
                ).fetchall()[::-1]
        if (
            first is None
            or float(first["seconds_left"]) < float(min_baseline_seconds_left)
            or float(first["spot_price"]) <= 0
            or float(snapshot["spot_price"]) <= 0
        ):
            return None
        volatility = rolling_volatility_per_sqrt_second(
            [(row["timestamp"], float(row["spot_price"])) for row in recent],
            min_return_samples=max(1, int(min_return_samples)),
            sigma_floor=float(sigma_floor),
        )
        if volatility is None:
            return None
        sigma, return_samples, floor_applied = volatility
        model_anchor = recent[0] if full_interval and recent else first
        anchor_timestamp = _timestamp_seconds(model_anchor["timestamp"])
        current_timestamp = _timestamp_seconds(snapshot.get("timestamp"))
        elapsed = (
            current_timestamp - anchor_timestamp
            if anchor_timestamp is not None and current_timestamp is not None
            else float(first["seconds_left"]) - float(snapshot["seconds_left"])
        )
        return {
            "anchor": float(model_anchor["spot_price"]),
            "baseline_seconds_left": float(first["seconds_left"]),
            "elapsed": max(0.0, elapsed),
            "sigma": sigma,
            "return_samples": float(return_samples),
            "floor_applied": floor_applied,
        }

    def strategy_k_probability_model(
        self, snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Model K from spot distance, time and volatility without market odds.

        The first Binance spot observation is mapped to the official feed strike.
        All volatility rows are capped at the signal timestamp so historical calls
        cannot accidentally use observations that arrived later.
        """
        market_id = int(snapshot["market_id"])
        signal_timestamp = snapshot.get("timestamp")
        current_spot = float(snapshot["spot_price"])
        official_strike = float(snapshot["start_price"])
        seconds_left = float(snapshot["seconds_left"])
        if (
            signal_timestamp is None
            or current_spot <= 0
            or official_strike <= 0
            or seconds_left < 0
        ):
            return None
        with self.lock:
            first = self.db.execute(
                """SELECT timestamp, spot_price, seconds_left FROM observations
                   WHERE market_id=? AND timestamp<=?
                   ORDER BY timestamp ASC, id ASC LIMIT 1""",
                (market_id, signal_timestamp),
            ).fetchone()
            recent = self.db.execute(
                """SELECT timestamp, spot_price FROM observations
                   WHERE market_id=? AND timestamp<=?
                   ORDER BY timestamp DESC, id DESC LIMIT ?""",
                (
                    market_id,
                    signal_timestamp,
                    max(2, int(cfg["strategy_k_lookback_observations"])),
                ),
            ).fetchall()[::-1]
        if (
            first is None
            or float(first["seconds_left"])
            < float(cfg["strategy_k_min_baseline_seconds_left"])
            or float(first["spot_price"]) <= 0
        ):
            return None
        volatility = rolling_volatility_per_sqrt_second(
            [(row["timestamp"], float(row["spot_price"])) for row in recent],
            min_return_samples=max(1, int(cfg["strategy_k_min_return_samples"])),
            sigma_floor=float(cfg["strategy_k_sigma_floor"]),
        )
        if volatility is None:
            return None
        sigma, return_samples, floor_applied = volatility
        anchor_spot = float(first["spot_price"])
        signed_log_distance = math.log(current_spot / anchor_spot)
        if signed_log_distance == 0:
            return None
        horizon = seconds_left + float(cfg["strategy_k_latency_seconds"])
        basis_uncertainty = float(cfg["strategy_k_basis_uncertainty_bps"]) / 10_000
        variance = sigma * sigma * horizon + basis_uncertainty * basis_uncertainty
        if horizon <= 0 or variance <= 0:
            return None
        z_score = abs(signed_log_distance) / math.sqrt(variance)
        raw_probability = normal_cdf(z_score)
        probability = temperature_calibrate_probability(
            raw_probability, float(cfg["strategy_k_calibration_slope"])
        )
        adjusted_spot = current_spot * official_strike / anchor_spot
        return {
            "side": "UP" if signed_log_distance > 0 else "DOWN",
            "probability": probability,
            "raw_probability": raw_probability,
            "signed_log_distance": signed_log_distance,
            "z_score": z_score,
            "variance": variance,
            "horizon": horizon,
            "sigma": sigma,
            "return_samples": return_samples,
            "floor_applied": floor_applied,
            "anchor_spot": anchor_spot,
            "baseline_seconds_left": float(first["seconds_left"]),
            "current_spot": current_spot,
            "official_strike": official_strike,
            "adjusted_spot": adjusted_spot,
            "initial_basis_bps": (anchor_spot / official_strike - 1.0) * 10_000,
            "basis_uncertainty": basis_uncertainty,
        }

    def strategy_k_entry_candidate(
        self,
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        fee_bps: int,
    ) -> dict[str, Any] | None:
        """Apply execution sanity checks to K without changing its model direction."""
        seconds_left = float(snapshot["seconds_left"])
        if not (
            float(cfg["strategy_k_min_seconds_left"])
            <= seconds_left
            <= float(cfg["strategy_k_max_seconds_left"])
        ):
            return None
        book_skew = snapshot.get("book_skew_ms")
        book_age = snapshot.get("book_age_ms")
        if (
            has_crossed_book(snapshot)
            or book_skew is None
            or book_age is None
            or not 0 <= float(book_skew) <= float(cfg["strategy_k_max_book_skew_ms"])
            or not 0 <= float(book_age) <= float(cfg["strategy_k_max_book_age_ms"])
        ):
            return None
        model = self.strategy_k_probability_model(snapshot, cfg)
        if model is None or model["probability"] < float(cfg["strategy_k_min_probability"]):
            return None

        # The model chooses exactly one side. A cheaper opposite ask is never
        # allowed to reverse that signal; price is only an execution cost here.
        side = str(model["side"])
        quoted_ask = snapshot.get(f"{side.lower()}_ask")
        quoted_bid = snapshot.get(f"{side.lower()}_bid")
        visible_size = snapshot.get(f"{side.lower()}_ask_size")
        if quoted_ask is None or quoted_bid is None or visible_size is None:
            return None
        quoted_ask = float(quoted_ask)
        quoted_bid = float(quoted_bid)
        visible_size = float(visible_size)
        if not (0 < quoted_ask < 1 and 0 <= quoted_bid <= 1 and visible_size > 0):
            return None
        spread = quoted_ask - quoted_bid
        if spread < 0 or spread > float(cfg["strategy_k_max_spread"]):
            return None
        slippage_rate = float(cfg["strategy_k_slippage_bps"]) / 10_000
        execution_entry = quoted_ask * (1.0 + slippage_rate)
        if execution_entry >= 1.0:
            return None
        required_shares = float(cfg["strategy_k_stake"]) / execution_entry
        if visible_size < required_shares:
            return None
        effective_cost = effective_taker_cost(execution_entry, fee_bps)
        model_edge = float(model["probability"]) - effective_cost
        required_edge = max(
            float(cfg["strategy_k_min_net_edge"]),
            float(cfg["strategy_k_spread_edge_multiplier"]) * spread,
        )
        if model_edge < required_edge:
            return None
        return {
            **model,
            "entry": execution_entry,
            "quoted_ask": quoted_ask,
            "quoted_bid": quoted_bid,
            "visible_size": visible_size,
            "required_shares": required_shares,
            "spread": spread,
            "slippage": execution_entry - quoted_ask,
            "taker_fee_per_share": taker_fee(1.0, execution_entry, fee_bps),
            "effective_cost": effective_cost,
            "edge": model_edge,
            "required_edge": required_edge,
            "book_skew_ms": float(book_skew),
            "book_age_ms": float(book_age),
        }

    def record_strategy_k_forecasts(
        self, snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> int:
        """Persist fixed-time model forecasts regardless of trade/cost eligibility."""
        seconds_left = float(snapshot["seconds_left"])
        checkpoints = [
            checkpoint
            for checkpoint in STRATEGY_K_FORECAST_CHECKPOINTS
            if 0 <= checkpoint - seconds_left <= 2.5
        ]
        if not checkpoints:
            return 0
        model = self.strategy_k_probability_model(snapshot, cfg)
        if model is None:
            return 0
        diagnostics = {
            "signal_timestamp": snapshot["timestamp"],
            "seconds_left": seconds_left,
            "side": model["side"],
            "signed_log_distance": model["signed_log_distance"],
            "z_score": model["z_score"],
            "variance": model["variance"],
            "horizon_seconds": model["horizon"],
            "raw_probability": model["raw_probability"],
            "calibrated_probability": model["probability"],
            "return_samples": model["return_samples"],
            "sigma_floor_applied": model["floor_applied"],
            "basis_uncertainty": model["basis_uncertainty"],
            "config": strategy_config_snapshot(cfg, "k"),
        }
        inserted = 0
        with self.lock:
            for checkpoint in checkpoints:
                cursor = self.db.execute(
                    """INSERT OR IGNORE INTO strategy_k_forecasts(
                           timestamp, topic_id, market_id, checkpoint_seconds,
                           seconds_left, signal_side, model_probability,
                           raw_probability, model_sigma, adjusted_spot,
                           official_strike, current_spot, anchor_spot,
                           initial_basis_bps, diagnostics_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot["timestamp"], int(snapshot["topic_id"]),
                        int(snapshot["market_id"]), checkpoint, seconds_left,
                        model["side"], model["probability"], model["raw_probability"],
                        model["sigma"], model["adjusted_spot"], model["official_strike"],
                        model["current_spot"], model["anchor_spot"],
                        model["initial_basis_bps"], json.dumps(diagnostics, sort_keys=True),
                    ),
                )
                inserted += cursor.rowcount
            self.db.commit()
        return inserted

    def volatility_momentum_candidate(
        self, snapshot: dict[str, Any], cfg: dict[str, float | bool]
    ) -> dict[str, Any] | None:
        """Strategy E2: post-baseline spot momentum normalized over that full interval."""
        seconds_left = float(snapshot["seconds_left"])
        if not (
            float(cfg["strategy_e2_min_seconds_left"])
            <= seconds_left
            <= float(cfg["strategy_e2_max_seconds_left"])
        ):
            return None
        book_skew = snapshot.get("book_skew_ms")
        book_age = snapshot.get("book_age_ms")
        if (
            has_crossed_book(snapshot)
            or book_skew is None
            or book_age is None
            or float(book_skew) > float(cfg["strategy_e2_max_book_skew_ms"])
            or float(book_age) > float(cfg["strategy_e2_max_book_age_ms"])
        ):
            return None
        context = self.spot_model_context(
            snapshot,
            lookback_observations=int(cfg["strategy_e2_lookback_observations"]),
            min_return_samples=int(cfg["strategy_e2_min_return_samples"]),
            sigma_floor=float(cfg["strategy_e2_sigma_floor"]),
            min_baseline_seconds_left=float(cfg["strategy_e2_min_baseline_seconds_left"]),
            full_interval=True,
        )
        if context is None or context["elapsed"] <= 0:
            return None
        signed_log_move = math.log(float(snapshot["spot_price"]) / context["anchor"])
        if signed_log_move == 0:
            return None
        move_z = abs(signed_log_move) / (
            context["sigma"] * math.sqrt(context["elapsed"])
        )
        if move_z < float(cfg["strategy_e2_min_move_z"]):
            return None
        side = "UP" if signed_log_move > 0 else "DOWN"
        entry = snapshot.get(f"{side.lower()}_ask")
        bid = snapshot.get(f"{side.lower()}_bid")
        visible_size = snapshot.get(f"{side.lower()}_ask_size")
        if entry is None or bid is None or visible_size is None:
            return None
        entry = float(entry)
        spread = max(0.0, entry - float(bid))
        required_shares = float(cfg["strategy_e2_stake"]) / entry
        if not (
            float(cfg["strategy_e2_min_entry"])
            <= entry
            <= float(cfg["strategy_e2_max_entry"])
        ):
            return None
        if (
            spread > float(cfg["strategy_e2_max_spread"])
            or float(visible_size) < required_shares
        ):
            return None
        return {
            "side": side,
            "entry": entry,
            "spread": spread,
            "move_z": move_z,
            "signed_log_move": signed_log_move,
            "quoted_bid": float(bid),
            "visible_size": float(visible_size),
            "book_skew_ms": float(book_skew),
            "book_age_ms": float(book_age),
            **context,
        }

    def conservative_model_candidate(
        self,
        snapshot: dict[str, Any],
        cfg: dict[str, float | bool],
        fee_bps: int,
    ) -> dict[str, Any] | None:
        """Strategy G: conservative official-strike probability-minus-cost model."""
        seconds_left = float(snapshot["seconds_left"])
        if not (
            float(cfg["strategy_g_min_seconds_left"])
            <= seconds_left
            <= float(cfg["strategy_g_max_seconds_left"])
        ):
            return None
        book_skew = snapshot.get("book_skew_ms")
        book_age = snapshot.get("book_age_ms")
        if (
            has_crossed_book(snapshot)
            or book_skew is None
            or book_age is None
            or float(book_skew) > float(cfg["strategy_g_max_book_skew_ms"])
            or float(book_age) > float(cfg["strategy_g_max_book_age_ms"])
        ):
            return None
        context = self.spot_model_context(
            snapshot,
            lookback_observations=int(cfg["strategy_g_lookback_observations"]),
            min_return_samples=int(cfg["strategy_g_min_return_samples"]),
            sigma_floor=float(cfg["strategy_g_sigma_floor"]),
            min_baseline_seconds_left=float(cfg["strategy_g_min_baseline_seconds_left"]),
        )
        if context is None:
            return None
        horizon = seconds_left + float(cfg["strategy_g_latency_seconds"])
        official_strike = float(snapshot["start_price"])
        if horizon <= 0 or official_strike <= 0:
            return None
        z_score = math.log(float(snapshot["spot_price"]) / official_strike) / (
            context["sigma"] * math.sqrt(horizon)
        )
        q_raw_up = normal_cdf(z_score)
        shrinkage = float(cfg["strategy_g_probability_shrinkage"])
        q_up = 0.5 + shrinkage * (q_raw_up - 0.5)
        probabilities = {"UP": q_up, "DOWN": 1.0 - q_up}
        raw_probabilities = {"UP": q_raw_up, "DOWN": 1.0 - q_raw_up}
        candidates: list[dict[str, Any]] = []
        for side in ("UP", "DOWN"):
            quoted_ask = snapshot.get(f"{side.lower()}_ask")
            bid = snapshot.get(f"{side.lower()}_bid")
            visible_size = snapshot.get(f"{side.lower()}_ask_size")
            if quoted_ask is None or bid is None or visible_size is None:
                continue
            quoted_ask = float(quoted_ask)
            bid = float(bid)
            spread = quoted_ask - bid
            if not (
                float(cfg["strategy_g_min_entry"])
                <= quoted_ask
                <= float(cfg["strategy_g_max_entry"])
            ):
                continue
            if spread > float(cfg["strategy_g_max_spread"]):
                continue
            slippage_rate = float(cfg["strategy_g_slippage_bps"]) / 10_000
            execution_entry = quoted_ask * (1.0 + slippage_rate)
            if execution_entry > 1.0:
                continue
            required_shares = float(cfg["strategy_g_stake"]) / execution_entry
            if float(visible_size) < required_shares:
                continue
            slippage = execution_entry - quoted_ask
            taker_fee_per_share = taker_fee(1.0, execution_entry, fee_bps)
            effective_cost = effective_taker_cost(execution_entry, fee_bps)
            conservative_edge = (
                probabilities[side]
                - float(cfg["strategy_g_uncertainty_margin"])
                - effective_cost
            )
            required_edge = max(
                float(cfg["strategy_g_min_net_edge"]),
                float(cfg["strategy_g_spread_edge_multiplier"]) * spread,
            )
            if conservative_edge < required_edge:
                continue
            candidates.append(
                {
                    "side": side,
                    "entry": execution_entry,
                    "quoted_ask": quoted_ask,
                    "quoted_bid": bid,
                    "visible_size": float(visible_size),
                    "spread": spread,
                    "probability": probabilities[side],
                    "raw_probability": raw_probabilities[side],
                    "effective_cost": effective_cost,
                    "slippage": slippage,
                    "taker_fee_per_share": taker_fee_per_share,
                    "edge": conservative_edge,
                    "required_edge": required_edge,
                    "raw_up_probability": q_raw_up,
                    "z_score": z_score,
                    "book_skew_ms": float(book_skew),
                    "book_age_ms": float(book_age),
                    "official_strike": official_strike,
                    **context,
                }
            )
        return max(candidates, key=lambda item: item["edge"]) if candidates else None

    def process_intraday_targets(self, market_id: int, up_bid: float | None, down_bid: float | None, fee_bps: int) -> None:
        bids = {"UP": up_bid, "DOWN": down_bid}
        with self.lock:
            trades = self.db.execute(
                "SELECT * FROM trades WHERE market_id=? AND strategy IN ('A', 'C', 'J') AND status='OPEN'",
                (market_id,),
            ).fetchall()
            for trade in trades:
                target = trade["target_price"]
                bid = bids[trade["side"]]
                if bid is None or bid < target:
                    continue
                exit_value = trade["shares"] * target
                exit_fee = taker_fee(trade["shares"], target, fee_bps)
                total_fees = trade["fees"] + exit_fee
                pnl = exit_value - trade["stake"] - total_fees
                self.db.execute(
                    """UPDATE trades SET status='TARGET_FILLED', exit_price=?, fees=?, pnl=?,
                       closed_at=? WHERE id=?""",
                    (target, total_fees, pnl, utc_iso(), trade["id"]),
                )
            self.db.commit()

    def _record_strategy_l_target_blocked(
        self,
        trade: sqlite3.Row,
        snapshot: dict[str, Any],
        *,
        reason: str,
        target: float,
        observed_bid: float | None,
        visible_bid_size: float | None,
    ) -> None:
        diagnostics = (
            json.loads(trade["diagnostics_json"])
            if trade["diagnostics_json"]
            else {}
        )
        prior_block = diagnostics.get("target_exit_blocked") or {}
        signal_timestamp = str(snapshot.get("timestamp") or utc_iso())
        diagnostics["target_exit_blocked"] = {
            "first_seen_at": prior_block.get("first_seen_at", signal_timestamp),
            "last_seen_at": signal_timestamp,
            "seconds_left": float(snapshot["seconds_left"]),
            "side": str(trade["side"]),
            "reason": reason,
            "target": target,
            "observed_bid": observed_bid,
            "visible_bid_size": visible_bid_size,
            "required_shares": float(trade["shares"]),
            "observations": int(prior_block.get("observations", 0)) + 1,
        }
        self.db.execute(
            """UPDATE trades SET diagnostics_json=?
               WHERE id=? AND status='OPEN'""",
            (json.dumps(diagnostics, sort_keys=True), trade["id"]),
        )

    def process_strategy_l_targets(
        self, snapshot: dict[str, Any], fee_bps: int
    ) -> None:
        """Fill L's frozen sell target only against sufficient visible bid depth."""
        del fee_bps  # Each leg freezes its entry-time taker fee rate in the trade row.
        market_id = int(snapshot["market_id"])
        bids = {"UP": snapshot.get("up_bid"), "DOWN": snapshot.get("down_bid")}
        bid_sizes = {
            "UP": snapshot.get("up_bid_size"),
            "DOWN": snapshot.get("down_bid_size"),
        }
        with self.lock:
            trades = self.db.execute(
                """SELECT * FROM trades
                   WHERE market_id=? AND strategy='L' AND status='OPEN'""",
                (market_id,),
            ).fetchall()
            for trade in trades:
                target = float(trade["target_price"])
                raw_bid = bids[str(trade["side"])]
                if raw_bid is None:
                    self._record_strategy_l_target_blocked(
                        trade,
                        snapshot,
                        reason="missing_bid",
                        target=target,
                        observed_bid=None,
                        visible_bid_size=None,
                    )
                    continue
                try:
                    observed_bid = float(raw_bid)
                except (TypeError, ValueError):
                    observed_bid = math.nan
                if not math.isfinite(observed_bid) or not 0 <= observed_bid <= 1:
                    self._record_strategy_l_target_blocked(
                        trade,
                        snapshot,
                        reason="invalid_bid",
                        target=target,
                        observed_bid=None,
                        visible_bid_size=None,
                    )
                    continue
                if observed_bid < target:
                    continue

                raw_bid_size = bid_sizes[str(trade["side"])]
                if raw_bid_size is None:
                    self._record_strategy_l_target_blocked(
                        trade,
                        snapshot,
                        reason="missing_bid_size",
                        target=target,
                        observed_bid=observed_bid,
                        visible_bid_size=None,
                    )
                    continue
                try:
                    visible_bid_size = float(raw_bid_size)
                except (TypeError, ValueError):
                    visible_bid_size = math.nan
                if not math.isfinite(visible_bid_size) or visible_bid_size <= 0:
                    self._record_strategy_l_target_blocked(
                        trade,
                        snapshot,
                        reason="invalid_bid_size",
                        target=target,
                        observed_bid=observed_bid,
                        visible_bid_size=None,
                    )
                    continue
                if visible_bid_size < float(trade["shares"]):
                    self._record_strategy_l_target_blocked(
                        trade,
                        snapshot,
                        reason="insufficient_bid_size",
                        target=target,
                        observed_bid=observed_bid,
                        visible_bid_size=visible_bid_size,
                    )
                    continue

                # This is a conservative observed-bid limit simulation. Even
                # when the current bid is better, credit only the frozen limit.
                trade_fee_bps = int(trade["fee_rate_bps"])
                exit_fee = taker_fee(float(trade["shares"]), target, trade_fee_bps)
                total_fees = float(trade["fees"]) + exit_fee
                exit_value = float(trade["shares"]) * target
                pnl = exit_value - float(trade["stake"]) - total_fees
                closed_at = utc_iso()
                diagnostics = (
                    json.loads(trade["diagnostics_json"])
                    if trade["diagnostics_json"]
                    else {}
                )
                diagnostics["target_exit"] = {
                    "execution_model": "observed_bid_full_depth_limit_v1",
                    "triggered_at": closed_at,
                    "signal_timestamp": str(snapshot.get("timestamp") or closed_at),
                    "seconds_left": float(snapshot["seconds_left"]),
                    "side": str(trade["side"]),
                    "target": target,
                    "observed_bid": observed_bid,
                    "visible_bid_size": visible_bid_size,
                    "exit_fee": exit_fee,
                    "total_fees": total_fees,
                    "pnl": pnl,
                }
                self.db.execute(
                    """UPDATE trades
                       SET status='TARGET_FILLED', exit_price=?, fees=?, pnl=?,
                           closed_at=?, note=note || ?, diagnostics_json=?
                       WHERE id=? AND status='OPEN'""",
                    (
                        target,
                        total_fees,
                        pnl,
                        closed_at,
                        f"; target limit filled {target:.3f} (observed bid={observed_bid:.3f})",
                        json.dumps(diagnostics, sort_keys=True),
                        trade["id"],
                    ),
                )
            self.db.commit()

    def _record_stop_loss_blocked(
        self,
        trade: sqlite3.Row,
        snapshot: dict[str, Any],
        *,
        threshold: float,
        reason: str,
        execution_bid: float | None,
        visible_bid_size: float | None,
    ) -> None:
        diagnostics = (
            json.loads(trade["diagnostics_json"])
            if trade["diagnostics_json"]
            else {}
        )
        prior_block = diagnostics.get("stop_loss_blocked") or {}
        signal_timestamp = str(snapshot.get("timestamp") or utc_iso())
        diagnostics["stop_loss_blocked"] = {
            "first_seen_at": prior_block.get("first_seen_at", signal_timestamp),
            "last_seen_at": signal_timestamp,
            "seconds_left": float(snapshot["seconds_left"]),
            "strategy": str(trade["strategy"]),
            "reason": reason,
            "threshold": threshold,
            "execution_bid": execution_bid,
            "visible_bid_size": visible_bid_size,
            "required_shares": float(trade["shares"]),
            "observations": int(prior_block.get("observations", 0)) + 1,
        }
        self.db.execute(
            """UPDATE trades SET diagnostics_json=?
               WHERE id=? AND status='OPEN'""",
            (json.dumps(diagnostics, sort_keys=True), trade["id"]),
        )

    def process_confidence_stop_losses(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        strategies: tuple[str, ...] = ("B", "B2"),
    ) -> None:
        """Apply the shared, full-depth stop model for confidence strategies."""
        selected = tuple(strategy for strategy in strategies if strategy in {"B", "B2"})
        if not selected:
            return
        market_id = int(snapshot["market_id"])
        cfg = self.config()
        bids = {"UP": snapshot.get("up_bid"), "DOWN": snapshot.get("down_bid")}
        bid_sizes = {
            "UP": snapshot.get("up_bid_size"),
            "DOWN": snapshot.get("down_bid_size"),
        }
        with self.lock:
            placeholders = ",".join("?" for _ in selected)
            trades = self.db.execute(
                f"""SELECT * FROM trades WHERE market_id=?
                    AND strategy IN ({placeholders}) AND status='OPEN'""",
                (market_id, *selected),
            ).fetchall()
            for trade in trades:
                strategy = str(trade["strategy"])
                config_key = f"strategy_{strategy.lower()}_stop_loss_price"
                diagnostics = (
                    json.loads(trade["diagnostics_json"])
                    if trade["diagnostics_json"]
                    else {}
                )
                frozen_threshold = diagnostics.get("stop_loss_price")
                if frozen_threshold is None and isinstance(
                    diagnostics.get("config"), dict
                ):
                    frozen_threshold = diagnostics["config"].get(config_key)
                try:
                    threshold = float(frozen_threshold)
                except (TypeError, ValueError):
                    threshold = float(cfg[config_key])
                if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                    threshold = float(cfg[config_key])

                raw_bid = bids[str(trade["side"])]
                if raw_bid is None:
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="missing_bid",
                        execution_bid=None,
                        visible_bid_size=None,
                    )
                    continue
                try:
                    exit_bid = float(raw_bid)
                except (TypeError, ValueError):
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="invalid_bid",
                        execution_bid=None,
                        visible_bid_size=None,
                    )
                    continue
                if not math.isfinite(exit_bid) or not 0 <= exit_bid <= 1:
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="invalid_bid",
                        execution_bid=None,
                        visible_bid_size=None,
                    )
                    continue
                # The configured threshold is exclusive: 0.60 does not trigger a
                # stop whose rule is "below 0.60".
                if exit_bid >= threshold:
                    continue

                # This simulator has one position row rather than a residual-lot
                # ledger. Require a known top bid that can execute the whole
                # stop instead of inventing liquidity for a full exit.
                raw_bid_size = bid_sizes[str(trade["side"])]
                if raw_bid_size is None:
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="missing_bid_size",
                        execution_bid=exit_bid,
                        visible_bid_size=None,
                    )
                    continue
                try:
                    visible_bid_size = float(raw_bid_size)
                except (TypeError, ValueError):
                    visible_bid_size = math.nan
                if not math.isfinite(visible_bid_size) or visible_bid_size < 0:
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="invalid_bid_size",
                        execution_bid=exit_bid,
                        visible_bid_size=None,
                    )
                    continue
                if visible_bid_size < float(trade["shares"]):
                    self._record_stop_loss_blocked(
                        trade,
                        snapshot,
                        threshold=threshold,
                        reason="insufficient_bid_size",
                        execution_bid=exit_bid,
                        visible_bid_size=visible_bid_size,
                    )
                    continue

                trade_fee_bps = int(trade["fee_rate_bps"])
                exit_fee = taker_fee(float(trade["shares"]), exit_bid, trade_fee_bps)
                total_fees = float(trade["fees"]) + exit_fee
                exit_value = float(trade["shares"]) * exit_bid
                pnl = exit_value - float(trade["stake"]) - total_fees
                closed_at = utc_iso()
                diagnostics["stop_loss"] = {
                    "triggered_at": closed_at,
                    "signal_timestamp": str(snapshot.get("timestamp") or closed_at),
                    "seconds_left": float(snapshot["seconds_left"]),
                    "strategy": strategy,
                    "threshold": threshold,
                    "execution_bid": exit_bid,
                    "visible_bid_size": visible_bid_size,
                    "exit_fee": exit_fee,
                    "total_fees": total_fees,
                    "pnl": pnl,
                }
                self.db.execute(
                    """UPDATE trades
                       SET status='STOP_LOSS_EXIT', exit_price=?, fees=?, pnl=?, closed_at=?,
                           note=note || ?, diagnostics_json=?
                       WHERE id=? AND status='OPEN'""",
                    (
                        exit_bid,
                        total_fees,
                        pnl,
                        closed_at,
                        f"; stop-loss bid={exit_bid:.3f} < {threshold:.3f}",
                        json.dumps(diagnostics, sort_keys=True),
                        trade["id"],
                    ),
                )
            self.db.commit()

    def process_b2_stop_loss(self, snapshot: dict[str, Any], fee_bps: int) -> None:
        """Backward-compatible B2-only wrapper for focused simulations/tests."""
        self.process_confidence_stop_losses(snapshot, fee_bps, strategies=("B2",))

    def process_b_stop_loss(self, snapshot: dict[str, Any], fee_bps: int) -> None:
        """B-only wrapper for focused simulations/tests."""
        self.process_confidence_stop_losses(snapshot, fee_bps, strategies=("B",))

    def process_time_arbitrage(self, snapshot: dict[str, Any], fee_bps: int) -> None:
        cfg = self.config()
        market_id = int(snapshot["market_id"])
        asks = {"UP": snapshot["up_ask"], "DOWN": snapshot["down_ask"]}
        bids = {"UP": snapshot["up_bid"], "DOWN": snapshot["down_bid"]}
        ask_sizes = {
            "UP": snapshot.get("up_ask_size"), "DOWN": snapshot.get("down_ask_size")
        }
        bid_sizes = {
            "UP": snapshot.get("up_bid_size"), "DOWN": snapshot.get("down_bid_size")
        }
        now = datetime.now(timezone.utc)
        with self.lock:
            trades = self.db.execute(
                """SELECT * FROM trades
                   WHERE market_id=? AND strategy IN ('D', 'F') AND status='OPEN'""",
                (market_id,),
            ).fetchall()
            for trade in trades:
                prefix = f"strategy_{str(trade['strategy']).lower()}"
                opposite = "DOWN" if trade["side"] == "UP" else "UP"
                second_ask = asks[opposite]
                second_size = ask_sizes[opposite]
                enough_second_depth = second_size is None or float(second_size) >= float(trade["shares"])
                if second_ask is not None and enough_second_depth:
                    net_pair_cost = effective_taker_cost(
                        float(trade["entry_price"]), fee_bps
                    ) + effective_taker_cost(float(second_ask), fee_bps)
                    if net_pair_cost <= float(cfg[f"{prefix}_max_pair_cost"]):
                        second_value = trade["shares"] * second_ask
                        second_fee = taker_fee(trade["shares"], second_ask, fee_bps)
                        total_fees = trade["fees"] + second_fee
                        pnl = trade["shares"] - trade["stake"] - second_value - total_fees
                        self.db.execute(
                            """UPDATE trades SET status='PAIRED_LOCKED', exit_price=?, fees=?, pnl=?,
                               closed_at=?, note=note || ? WHERE id=?""",
                            (
                                second_ask, total_fees, pnl, utc_iso(),
                                f"; paired {opposite}@{second_ask:.3f} net_pair={net_pair_cost:.3f}",
                                trade["id"],
                            ),
                        )
                        continue

                opened_at = datetime.fromisoformat(trade["opened_at"])
                waited = max(0.0, (now - opened_at).total_seconds())
                timed_out = waited >= float(cfg[f"{prefix}_max_wait_seconds"])
                forced_out = float(snapshot["seconds_left"]) <= float(
                    cfg[f"{prefix}_force_exit_seconds"]
                )
                if not (timed_out or forced_out):
                    continue
                exit_bid = bids[trade["side"]]
                exit_size = bid_sizes[trade["side"]]
                if exit_bid is None or (
                    exit_size is not None and float(exit_size) < float(trade["shares"])
                ):
                    continue
                exit_value = trade["shares"] * exit_bid
                exit_fee = taker_fee(trade["shares"], exit_bid, fee_bps)
                total_fees = trade["fees"] + exit_fee
                pnl = exit_value - trade["stake"] - total_fees
                reason = "force-exit before settlement" if forced_out else "second-leg timeout"
                self.db.execute(
                    """UPDATE trades SET status='TIMEOUT_EXIT', exit_price=?, fees=?, pnl=?,
                       closed_at=?, note=note || ? WHERE id=?""",
                    (exit_bid, total_fees, pnl, utc_iso(), f"; {reason} after {waited:.1f}s", trade["id"]),
                )
            self.db.commit()

    def next_pending_settlement(self) -> dict[str, Any] | None:
        """Return the oldest reconcilable proxy settlement, preserving market order."""
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM market_settlements
                   WHERE status='PENDING' AND topic_id IS NOT NULL AND start_price IS NOT NULL
                   ORDER BY first_settled_at ASC, market_id ASC LIMIT 1"""
            ).fetchone()
        return dict(row) if row is not None else None

    def next_untracked_open_settlement(
        self, exclude_market_id: int | None = None
    ) -> dict[str, Any] | None:
        """Find an OPEN market lost across restart before settlement was recorded."""
        with self.lock:
            row = self.db.execute(
                """SELECT t.market_id, MAX(t.topic_id) AS topic_id,
                          MAX(o.start_price) AS start_price,
                          MIN(t.opened_at) AS first_opened_at
                   FROM trades t
                   LEFT JOIN market_settlements s ON s.market_id=t.market_id
                   LEFT JOIN observations o ON o.market_id=t.market_id
                   WHERE t.status='OPEN' AND s.market_id IS NULL
                     AND (? IS NULL OR t.market_id<>?)
                   GROUP BY t.market_id
                   HAVING MAX(t.topic_id) IS NOT NULL
                      AND MAX(o.start_price) IS NOT NULL
                   ORDER BY first_opened_at ASC, t.market_id ASC
                   LIMIT 1""",
                (exclude_market_id, exclude_market_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_settlement_checked(self, market_id: int) -> None:
        with self.lock:
            self.db.execute(
                """UPDATE market_settlements
                   SET check_attempts=check_attempts+1, last_checked_at=?
                   WHERE market_id=? AND status='PENDING'""",
                (utc_iso(), market_id),
            )
            self.db.commit()

    def _settle_mx_positions(self, market_id: int, winner: str) -> None:
        """Close every MX residual only from an official market outcome."""
        self._mx_expire_entries(market_id, float("inf"), force=True)
        positions = self.db.execute(
            """SELECT * FROM strategy_mx_positions
               WHERE market_id=? ORDER BY strategy""",
            (market_id,),
        ).fetchall()
        for position in positions:
            strategy = str(position["strategy"])
            remaining = max(0.0, float(position["remaining_shares"]))
            if remaining <= 1e-12:
                self._mx_sync_position_status(
                    strategy, market_id, close_reason="official settlement"
                )
                continue
            now = utc_iso()
            self.db.execute(
                """UPDATE strategy_mx_orders
                   SET status='CANCELLED_SETTLEMENT',
                       reason='residual moved to official settlement', updated_at=?
                   WHERE strategy=? AND market_id=? AND action='EXIT'
                     AND status IN ('OPEN','PARTIAL')""",
                (now, strategy, market_id),
            )
            self.db.execute(
                """INSERT OR IGNORE INTO strategy_mx_orders(
                       strategy, market_id, action, stage_key, status,
                       limit_price, requested_shares, filled_shares,
                       reason, created_at, updated_at
                   ) VALUES (?, ?, 'EXIT', 'SETTLEMENT', 'OPEN', NULL, ?, 0,
                             'official settlement residual', ?, ?)""",
                (strategy, market_id, remaining, now, now),
            )
            order = self.db.execute(
                """SELECT * FROM strategy_mx_orders
                   WHERE strategy=? AND market_id=? AND action='EXIT'
                     AND stage_key='SETTLEMENT'""",
                (strategy, market_id),
            ).fetchone()
            current = self.db.execute(
                """SELECT * FROM strategy_mx_positions
                   WHERE strategy=? AND market_id=?""",
                (strategy, market_id),
            ).fetchone()
            assert order is not None and current is not None
            price = 1.0 if current["side"] == winner else 0.0
            self._mx_insert_fill(
                order,
                current,
                price=price,
                shares=remaining,
                fee_bps=0,
                snapshot={
                    "timestamp": now,
                    "market_id": market_id,
                    "signal_event_sequence": f"official-settlement:{market_id}",
                },
                context={
                    "signal_event_sequence": f"official-settlement:{market_id}",
                },
                reason="official settlement residual",
            )
            self.db.execute(
                """UPDATE strategy_mx_positions
                   SET status=?, close_reason='official settlement',
                       updated_at=?, closed_at=COALESCE(closed_at, ?)
                   WHERE strategy=? AND market_id=?""",
                (
                    "SETTLED_WIN" if price == 1.0 else "SETTLED_LOSS",
                    now,
                    now,
                    strategy,
                    market_id,
                ),
            )

    def settle_market(
        self,
        market_id: int,
        winner: str,
        official: bool,
        *,
        topic_id: int | None = None,
        start_price: float | None = None,
        end_price: float | None = None,
    ) -> None:
        if winner not in {"UP", "DOWN"}:
            raise ValueError("winner must be UP or DOWN")
        cfg = self.config()
        with self.lock:
            prior = self.db.execute(
                "SELECT * FROM market_settlements WHERE market_id=?", (market_id,)
            ).fetchone()
            if prior is not None and prior["status"] == "OFFICIAL":
                if official and prior["official_winner"] != winner:
                    raise ValueError("official settlement winner changed")
                if official:
                    self._process_ready_strategy_h_settlements(cfg)
                    self.db.commit()
                return
            if prior is not None and not official:
                return

            observed = self.db.execute(
                """SELECT topic_id, start_price FROM observations
                   WHERE market_id=? ORDER BY id DESC LIMIT 1""",
                (market_id,),
            ).fetchone()
            if topic_id is None:
                topic_id = (
                    int(prior["topic_id"])
                    if prior is not None and prior["topic_id"] is not None
                    else int(observed["topic_id"]) if observed is not None else None
                )
            if start_price is None:
                start_price = (
                    float(prior["start_price"])
                    if prior is not None and prior["start_price"] is not None
                    else float(observed["start_price"]) if observed is not None else None
                )

            settled_at = utc_iso()
            if prior is None:
                self.db.execute(
                    """INSERT INTO market_settlements(
                           market_id, topic_id, start_price, proxy_winner, official_winner,
                           official_end_price, status, first_settled_at, official_settled_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        market_id,
                        topic_id,
                        start_price,
                        None if official else winner,
                        winner if official else None,
                        end_price if official else None,
                        "OFFICIAL" if official else "PENDING",
                        settled_at,
                        settled_at if official else None,
                    ),
                )
            else:
                self.db.execute(
                    """UPDATE market_settlements
                       SET topic_id=COALESCE(topic_id, ?),
                           start_price=COALESCE(start_price, ?),
                           official_winner=?, official_end_price=?, status='OFFICIAL',
                           official_settled_at=?
                       WHERE market_id=?""",
                    (topic_id, start_price, winner, end_price, settled_at, market_id),
                )

            trades = self.db.execute(
                """SELECT * FROM trades
                   WHERE market_id=? AND status IN ('OPEN', 'SETTLED_WIN', 'SETTLED_LOSS')""",
                (market_id,),
            ).fetchall()
            h_result: str | None = None
            for trade in trades:
                won = trade["side"] == winner
                payout = trade["shares"] if won else 0.0
                pnl = payout - trade["stake"] - trade["fees"]
                status = "SETTLED_WIN" if won else "SETTLED_LOSS"
                if official and trade["status"] != "OPEN":
                    source = "official endPrice reconciliation"
                else:
                    source = "official endPrice" if official else "spot proxy"
                self.db.execute(
                    """UPDATE trades SET status=?, exit_price=?, pnl=?, closed_at=?,
                       note=note || ? WHERE id=?""",
                    (status, 1.0 if won else 0.0, pnl, utc_iso(), f"; settled via {source}", trade["id"]),
                )
                if trade["strategy"] == "H":
                    h_result = "WIN" if won else "LOSS"
            if official:
                self.db.execute(
                    """UPDATE confirmation_add_shadow_state
                          SET status='OFFICIAL', officially_settled_at=?, updated_at=?
                        WHERE market_id=? AND status<>'OFFICIAL'""",
                    (settled_at, settled_at, market_id),
                )
                self._settle_mx_positions(market_id, winner)
                self.db.execute(
                    """UPDATE strategy_k_forecasts
                       SET official_winner=?,
                           forecast_correct=CASE WHEN signal_side=? THEN 1 ELSE 0 END,
                           settled_official=1
                       WHERE market_id=?""",
                    (winner, winner, market_id),
                )
                self._process_ready_strategy_h_settlements(cfg)
            else:
                self._process_strategy_h_settlement(
                    market_id=market_id,
                    winner=winner,
                    official=False,
                    h_result=h_result,
                    cfg=cfg,
                )
            self.db.commit()
            if official and MARKET_OBSERVER is not None:
                try:
                    settled_fill = summarize_m01_settled_fills(
                        [
                            dict(row)
                            for row in self.db.execute(
                                "SELECT * FROM trades WHERE market_id=?",
                                (market_id,),
                            ).fetchall()
                        ]
                    )
                    MARKET_OBSERVER.record_settlement(
                        market_id=market_id,
                        winner=winner,
                        start_price=start_price,
                        m01_filled=bool(settled_fill["m01_filled"]),
                        m01_won=bool(settled_fill["m01_won"]),
                        m01_fill_price=settled_fill["m01_fill_price"],
                        m01_avg_fee_rate_bps=settled_fill[
                            "m01_fee_rate_bps"
                        ],
                        settled_at=settled_at,
                    )
                except Exception as exc:
                    # Observation failures must never roll back or otherwise
                    # influence a completed paper/live settlement.
                    MARKET_OBSERVER.last_error = str(exc)

    def maybe_enter(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        include_m_series: bool = True,
    ) -> None:
        cfg = self.config()
        market_id = int(snapshot["market_id"])
        elapsed = 300 - float(snapshot["seconds_left"])
        asks = {"UP": snapshot["up_ask"], "DOWN": snapshot["down_ask"]}

        # Forecast checkpoints are independent of whether the order price later
        # permits a K trade, avoiding a dataset made only of executed signals.
        if cfg["strategy_k_enabled"]:
            self.record_strategy_k_forecasts(snapshot, cfg)

        if cfg["strategy_a_enabled"] and not self.has_trade("A", market_id):
            valid = [(side, price) for side, price in asks.items() if price is not None]
            if len(valid) == 2:
                gap = abs(valid[0][1] - valid[1][1])
                side, entry = min(valid, key=lambda item: item[1])
                if (
                    elapsed <= float(cfg["strategy_a_window_seconds"])
                    and gap >= float(cfg["strategy_a_min_gap"])
                    and entry <= float(cfg["strategy_a_max_entry"])
                    and entry < float(cfg["strategy_a_target"])
                ):
                    self.open_trade(
                        strategy="A", topic_id=snapshot["topic_id"], market_id=market_id,
                        side=side, entry=entry, target=float(cfg["strategy_a_target"]),
                        stake=float(cfg["strategy_a_stake"]), fee_rate_bps=fee_bps,
                        note=f"first-2m imbalance gap={gap:.3f}",
                    )

        if cfg["strategy_b_enabled"] and not self.has_trade("B", market_id):
            valid = [(side, price) for side, price in asks.items() if price is not None]
            if valid:
                side, entry = max(valid, key=lambda item: item[1])
                entry_bid = snapshot.get(f"{side.lower()}_bid")
                entry_bid_value = float(entry_bid) if entry_bid is not None else None
                if (
                    snapshot["seconds_left"] <= float(cfg["strategy_b_last_seconds"])
                    and entry >= float(cfg["strategy_b_min_price"])
                    and entry <= float(cfg["strategy_b_max_price"])
                    and entry_bid_value is not None
                    and math.isfinite(entry_bid_value)
                    and 0 <= entry_bid_value <= entry
                    and entry_bid_value >= float(cfg["strategy_b_stop_loss_price"])
                ):
                    self.open_trade(
                        strategy="B", topic_id=snapshot["topic_id"], market_id=market_id,
                        side=side, entry=entry, target=None, stake=float(cfg["strategy_b_stake"]),
                        fee_rate_bps=fee_bps,
                        strategy_version="B_v2_stop_loss",
                        diagnostics={
                            "signal_timestamp": snapshot["timestamp"],
                            "seconds_left": float(snapshot["seconds_left"]),
                            "quoted_ask": entry,
                            "quoted_bid": entry_bid_value,
                            "requested_stake": float(cfg["strategy_b_stake"]),
                            "stop_loss_price": float(cfg["strategy_b_stop_loss_price"]),
                            "config": strategy_config_snapshot(cfg, "b"),
                        },
                        note=(
                            f"last-{cfg['strategy_b_last_seconds']:.0f}s confidence entry; "
                            f"stop<{float(cfg['strategy_b_stop_loss_price']):.3f}"
                        ),
                    )

        if cfg["strategy_b2_enabled"] and not self.has_trade("B2", market_id):
            candidate_b2 = self.strategy_b2_entry_candidate(snapshot, cfg)
            if candidate_b2 is not None:
                self.open_trade(
                    strategy="B2",
                    topic_id=snapshot["topic_id"],
                    market_id=market_id,
                    side=str(candidate_b2["side"]),
                    entry=float(candidate_b2["entry"]),
                    target=None,
                    stake=float(candidate_b2["filled_stake"]),
                    fee_rate_bps=fee_bps,
                    strategy_version="B2_v1_time_scaled_stop_loss",
                    diagnostics={
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": candidate_b2["seconds_left"],
                        "progress": candidate_b2["progress"],
                        "risk_level": candidate_b2["risk_level"],
                        "size_curve": candidate_b2["curve"],
                        "requested_stake": candidate_b2["requested_stake"],
                        "requested_shares": candidate_b2["requested_shares"],
                        "filled_stake": candidate_b2["filled_stake"],
                        "filled_shares": candidate_b2["filled_shares"],
                        "fill_ratio": candidate_b2["fill_ratio"],
                        "partial_fill": candidate_b2["partial_fill"],
                        "visible_ask_size": candidate_b2["visible_ask_size"],
                        "quoted_ask": candidate_b2["entry"],
                        "quoted_bid": candidate_b2["bid"],
                        "stop_loss_price": float(cfg["strategy_b2_stop_loss_price"]),
                        "config": strategy_config_snapshot(cfg, "b2"),
                    },
                    note=(
                        "time-scaled late confidence; "
                        f"left={float(candidate_b2['seconds_left']):.1f}s; "
                        f"progress={float(candidate_b2['progress']):.3f}; "
                        f"risk={float(candidate_b2['risk_level']):.3f}; "
                        f"stake={float(candidate_b2['filled_stake']):.2f}/"
                        f"{float(candidate_b2['requested_stake']):.2f}; "
                        f"stop<{float(cfg['strategy_b2_stop_loss_price']):.3f}"
                    ),
                )

        if cfg["strategy_c_enabled"] and not self.has_trade("C", market_id):
            valid = [(side, price) for side, price in asks.items() if price is not None]
            if len(valid) == 2:
                gap = abs(valid[0][1] - valid[1][1])
                side, entry = min(valid, key=lambda item: item[1])
                target = entry * float(cfg["strategy_c_target_multiplier"])
                if (
                    elapsed <= float(cfg["strategy_c_window_seconds"])
                    and gap >= float(cfg["strategy_c_min_gap"])
                    and entry <= float(cfg["strategy_c_max_entry"])
                    and target <= 1.0
                ):
                    self.open_trade(
                        strategy="C", topic_id=snapshot["topic_id"], market_id=market_id,
                        side=side, entry=entry, target=target,
                        stake=float(cfg["strategy_c_stake"]), fee_rate_bps=fee_bps,
                        note=f"first-2m dynamic target {cfg['strategy_c_target_multiplier']:.2f}x; gap={gap:.3f}",
                    )

        if cfg["strategy_e_enabled"] and not self.has_trade("E", market_id):
            candidate = self.momentum_candidate(snapshot, cfg)
            if candidate is not None:
                side, entry, move_bps, spread, baseline_seconds_left = candidate
                self.open_trade(
                    strategy="E", topic_id=snapshot["topic_id"], market_id=market_id,
                    side=side, entry=entry, target=None,
                    stake=float(cfg["strategy_e_stake"]), fee_rate_bps=fee_bps,
                    note=(
                        f"spot+market confirmation; same-source move={move_bps:+.2f}bps; "
                        f"spread={spread:.3f}; baseline_left={baseline_seconds_left:.1f}s"
                    ),
                )

        if cfg["strategy_e2_enabled"] and not self.has_trade("E2", market_id):
            candidate_e2 = self.volatility_momentum_candidate(snapshot, cfg)
            if candidate_e2 is not None:
                self.open_trade(
                    strategy="E2", topic_id=snapshot["topic_id"], market_id=market_id,
                    side=candidate_e2["side"], entry=candidate_e2["entry"], target=None,
                    stake=float(cfg["strategy_e2_stake"]), fee_rate_bps=fee_bps,
                    strategy_version="E2_v1_full_interval", model_sigma=candidate_e2["sigma"],
                    diagnostics={
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "start_price": float(snapshot["start_price"]),
                        "current_spot": float(snapshot["spot_price"]),
                        "baseline_spot": candidate_e2["anchor"],
                        "quoted_ask": candidate_e2["entry"],
                        "quoted_bid": candidate_e2["quoted_bid"],
                        "visible_ask_size": candidate_e2["visible_size"],
                        "move_z": candidate_e2["move_z"],
                        "signed_log_move": candidate_e2["signed_log_move"],
                        "spread": candidate_e2["spread"],
                        "elapsed_seconds": candidate_e2["elapsed"],
                        "return_samples": int(candidate_e2["return_samples"]),
                        "sigma": candidate_e2["sigma"],
                        "sigma_floor_applied": candidate_e2["floor_applied"],
                        "book_skew_ms": candidate_e2["book_skew_ms"],
                        "book_age_ms": candidate_e2["book_age_ms"],
                        "config": strategy_config_snapshot(cfg, "e2"),
                    },
                    note=(
                        f"post-baseline full-interval momentum; z={candidate_e2['move_z']:.3f}; "
                        f"spread={candidate_e2['spread']:.3f}; "
                        f"sigma={candidate_e2['sigma']:.8f}"
                    ),
                )

        if cfg["strategy_g_enabled"] and not self.has_trade("G", market_id):
            candidate_g = self.conservative_model_candidate(snapshot, cfg, fee_bps)
            if candidate_g is not None:
                self.open_trade(
                    strategy="G", topic_id=snapshot["topic_id"], market_id=market_id,
                    side=candidate_g["side"], entry=candidate_g["entry"], target=None,
                    stake=float(cfg["strategy_g_stake"]), fee_rate_bps=fee_bps,
                    strategy_version="G_v1_official_strike",
                    model_probability=candidate_g["probability"],
                    model_edge=candidate_g["edge"], model_sigma=candidate_g["sigma"],
                    diagnostics={
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "start_price": float(snapshot["start_price"]),
                        "current_spot": float(snapshot["spot_price"]),
                        "first_observed_spot": candidate_g["anchor"],
                        "initial_basis_bps": (
                            candidate_g["anchor"] / candidate_g["official_strike"] - 1.0
                        ) * 10_000,
                        "quoted_ask": candidate_g["quoted_ask"],
                        "quoted_bid": candidate_g["quoted_bid"],
                        "visible_ask_size": candidate_g["visible_size"],
                        "execution_entry": candidate_g["entry"],
                        "slippage_bps": float(cfg["strategy_g_slippage_bps"]),
                        "slippage_per_share": candidate_g["slippage"],
                        "taker_fee_per_share": candidate_g["taker_fee_per_share"],
                        "q_raw": candidate_g["raw_probability"],
                        "q_shrunk": candidate_g["probability"],
                        "q_raw_up": candidate_g["raw_up_probability"],
                        "uncertainty_margin": float(cfg["strategy_g_uncertainty_margin"]),
                        "z_score": candidate_g["z_score"],
                        "effective_cost": candidate_g["effective_cost"],
                        "edge_threshold": candidate_g["required_edge"],
                        "conservative_edge": candidate_g["edge"],
                        "spread": candidate_g["spread"],
                        "book_skew_ms": candidate_g["book_skew_ms"],
                        "book_age_ms": candidate_g["book_age_ms"],
                        "official_strike": candidate_g["official_strike"],
                        "return_samples": int(candidate_g["return_samples"]),
                        "sigma": candidate_g["sigma"],
                        "sigma_floor_applied": candidate_g["floor_applied"],
                        "config": strategy_config_snapshot(cfg, "g"),
                    },
                    note=(
                        f"conservative official-strike model; q={candidate_g['probability']:.4f}; "
                        f"cost={candidate_g['effective_cost']:.4f}; "
                        f"conservative_edge={candidate_g['edge']:.4f}"
                    ),
                )

        if cfg["strategy_k_enabled"] and not self.has_trade("K", market_id):
            candidate_k = self.strategy_k_entry_candidate(snapshot, cfg, fee_bps)
            if candidate_k is not None:
                self.open_trade(
                    strategy="K", topic_id=snapshot["topic_id"], market_id=market_id,
                    side=candidate_k["side"], entry=candidate_k["entry"], target=None,
                    stake=float(cfg["strategy_k_stake"]), fee_rate_bps=fee_bps,
                    strategy_version="K_v1_basis_adjusted_terminal_prob",
                    model_probability=candidate_k["probability"],
                    model_edge=candidate_k["edge"], model_sigma=candidate_k["sigma"],
                    diagnostics={
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "signal_source": "binance_spot_distance_only",
                        "direction_uses_order_price": False,
                        "official_strike": candidate_k["official_strike"],
                        "first_observed_spot": candidate_k["anchor_spot"],
                        "current_spot": candidate_k["current_spot"],
                        "basis_adjusted_spot": candidate_k["adjusted_spot"],
                        "initial_basis_bps": candidate_k["initial_basis_bps"],
                        "basis_uncertainty_bps": float(
                            cfg["strategy_k_basis_uncertainty_bps"]
                        ),
                        "signed_log_distance": candidate_k["signed_log_distance"],
                        "z_score": candidate_k["z_score"],
                        "variance": candidate_k["variance"],
                        "horizon_seconds": candidate_k["horizon"],
                        "q_raw": candidate_k["raw_probability"],
                        "q_calibrated": candidate_k["probability"],
                        "calibration_slope": float(cfg["strategy_k_calibration_slope"]),
                        "quoted_ask": candidate_k["quoted_ask"],
                        "quoted_bid": candidate_k["quoted_bid"],
                        "visible_ask_size": candidate_k["visible_size"],
                        "required_shares": candidate_k["required_shares"],
                        "execution_entry": candidate_k["entry"],
                        "slippage_bps": float(cfg["strategy_k_slippage_bps"]),
                        "slippage_per_share": candidate_k["slippage"],
                        "taker_fee_per_share": candidate_k["taker_fee_per_share"],
                        "effective_cost": candidate_k["effective_cost"],
                        "model_edge": candidate_k["edge"],
                        "edge_threshold": candidate_k["required_edge"],
                        "spread": candidate_k["spread"],
                        "book_skew_ms": candidate_k["book_skew_ms"],
                        "book_age_ms": candidate_k["book_age_ms"],
                        "baseline_seconds_left": candidate_k["baseline_seconds_left"],
                        "return_samples": candidate_k["return_samples"],
                        "sigma": candidate_k["sigma"],
                        "sigma_floor_applied": candidate_k["floor_applied"],
                        "config": strategy_config_snapshot(cfg, "k"),
                    },
                    note=(
                        "basis-adjusted spot-distance terminal model; "
                        f"{candidate_k['side']} q={candidate_k['probability']:.4f}; "
                        f"cost={candidate_k['effective_cost']:.4f}; "
                        f"edge={candidate_k['edge']:.4f}"
                    ),
                )

        if (
            cfg["strategy_l_enabled"]
            and 0 <= elapsed <= float(cfg["strategy_l_window_seconds"])
        ):
            try:
                l_book = {
                    key: float(snapshot[key])
                    for key in ("up_ask", "up_bid", "down_ask", "down_bid")
                }
                book_skew = float(snapshot["book_skew_ms"])
                book_age = float(snapshot["book_age_ms"])
            except (KeyError, TypeError, ValueError):
                l_book = {}
                book_skew = math.inf
                book_age = math.inf
            book_is_executable = (
                len(l_book) == 4
                and all(math.isfinite(value) for value in l_book.values())
                and math.isfinite(book_skew)
                and math.isfinite(book_age)
                and 0 <= book_skew <= float(cfg["strategy_l_max_book_skew_ms"])
                and 0 <= book_age <= float(cfg["strategy_l_max_book_age_ms"])
                and 0 < l_book["up_ask"] <= 1
                and 0 <= l_book["up_bid"] <= l_book["up_ask"]
                and 0 < l_book["down_ask"] <= 1
                and 0 <= l_book["down_bid"] <= l_book["down_ask"]
            )
            if book_is_executable:
                total_budget = float(cfg["strategy_l_total_stake"])
                leg_budget = total_budget / 2.0
                target = float(cfg["strategy_l_target"])
                for side in ("UP", "DOWN"):
                    if self.has_trade_side("L", market_id, side):
                        continue
                    entry = l_book[f"{side.lower()}_ask"]
                    if entry > float(cfg["strategy_l_max_entry"]):
                        continue
                    raw_visible_size = snapshot.get(f"{side.lower()}_ask_size")
                    try:
                        visible_size = float(raw_visible_size)
                    except (TypeError, ValueError):
                        continue
                    fill = top_ask_fill(leg_budget, entry, visible_size)
                    if fill is None:
                        continue
                    self.open_trade(
                        strategy="L",
                        topic_id=snapshot["topic_id"],
                        market_id=market_id,
                        side=side,
                        entry=entry,
                        target=target,
                        stake=float(fill["filled_stake"]),
                        fee_rate_bps=fee_bps,
                        strategy_version="L_v1_two_leg_target",
                        diagnostics={
                            "execution_model": "top_ask_partial_fill_v1",
                            "target_execution_model": (
                                "observed_bid_full_depth_limit_v1"
                            ),
                            "signal_timestamp": snapshot.get("timestamp"),
                            "seconds_left": float(snapshot["seconds_left"]),
                            "elapsed_seconds": elapsed,
                            "leg": side,
                            "quoted_ask": entry,
                            "quoted_bid": l_book[f"{side.lower()}_bid"],
                            "target_price": target,
                            "total_budget": total_budget,
                            "leg_budget": leg_budget,
                            "visible_ask_size": visible_size,
                            "requested_stake": fill["requested_stake"],
                            "requested_shares": fill["requested_shares"],
                            "filled_stake": fill["filled_stake"],
                            "filled_shares": fill["filled_shares"],
                            "fill_ratio": fill["fill_ratio"],
                            "partial_fill": fill["partial_fill"],
                            "book_skew_ms": book_skew,
                            "book_age_ms": book_age,
                            "config": strategy_config_snapshot(cfg, "l"),
                        },
                        note=(
                            f"first-{float(cfg['strategy_l_window_seconds']):.0f}s "
                            f"dual-leg target; {side}@{entry:.3f} -> {target:.3f}; "
                            f"{'partial' if fill['partial_fill'] else 'full'} fill "
                            f"{float(fill['filled_shares']):.4f}/"
                            f"{float(fill['requested_shares']):.4f} shares"
                        ),
                    )

        if include_m_series:
            self.maybe_enter_m_series(snapshot, fee_bps)

        h_state = self.strategy_h_state()
        if (
            cfg["strategy_h_enabled"]
            and h_state["mode"] == "ARMED"
            and not self.has_trade("H", market_id)
        ):
            candidate_h = self.strategy_h_entry_candidate(snapshot, cfg)
            if candidate_h is not None:
                self.open_trade(
                    strategy="H",
                    topic_id=snapshot["topic_id"],
                    market_id=market_id,
                    side=candidate_h["side"],
                    entry=candidate_h["entry"],
                    target=None,
                    stake=float(candidate_h["filled_stake"]),
                    fee_rate_bps=fee_bps,
                    strategy_version="H_v2_partial_fill",
                    diagnostics={
                        "execution_model": "top_ask_partial_fill_v1",
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "quoted_ask": candidate_h["entry"],
                        "quoted_bid": candidate_h["bid"],
                        "visible_ask_size": candidate_h["visible_size"],
                        "required_shares": candidate_h["required_shares"],
                        "requested_stake": candidate_h["requested_stake"],
                        "requested_shares": candidate_h["requested_shares"],
                        "filled_stake": candidate_h["filled_stake"],
                        "filled_shares": candidate_h["filled_shares"],
                        "fill_ratio": candidate_h["fill_ratio"],
                        "partial_fill": candidate_h["partial_fill"],
                        "book_skew_ms": candidate_h["book_skew_ms"],
                        "book_age_ms": candidate_h["book_age_ms"],
                        "armed_at": h_state["armedAt"],
                        "arming_reversal_streak": h_state["reversalStreak"],
                        "config": strategy_config_snapshot(cfg, "h"),
                    },
                    note=(
                        f"armed reversal-sniper longshot; last-"
                        f"{cfg['strategy_h_entry_window_seconds']:.0f}s "
                        f"{candidate_h['side']}@{candidate_h['entry']:.3f}; "
                        f"{'partial' if candidate_h['partial_fill'] else 'full'} fill "
                        f"{candidate_h['filled_shares']:.4f}/"
                        f"{candidate_h['requested_shares']:.4f} shares"
                    ),
                )

        if cfg["strategy_i_enabled"] and not self.has_trade("I", market_id):
            candidate_i = self.strategy_i_entry_candidate(snapshot, cfg)
            if candidate_i is not None:
                self.open_trade(
                    strategy="I",
                    topic_id=snapshot["topic_id"],
                    market_id=market_id,
                    side=candidate_i["side"],
                    entry=candidate_i["entry"],
                    target=None,
                    stake=float(candidate_i["filled_stake"]),
                    fee_rate_bps=fee_bps,
                    strategy_version="I_v2_partial_fill",
                    diagnostics={
                        "execution_model": "top_ask_partial_fill_v1",
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "quoted_ask": candidate_i["entry"],
                        "quoted_bid": candidate_i["bid"],
                        "visible_ask_size": candidate_i["visible_size"],
                        "required_shares": candidate_i["required_shares"],
                        "requested_stake": candidate_i["requested_stake"],
                        "requested_shares": candidate_i["requested_shares"],
                        "filled_stake": candidate_i["filled_stake"],
                        "filled_shares": candidate_i["filled_shares"],
                        "fill_ratio": candidate_i["fill_ratio"],
                        "partial_fill": candidate_i["partial_fill"],
                        "book_skew_ms": candidate_i["book_skew_ms"],
                        "book_age_ms": candidate_i["book_age_ms"],
                        "config": strategy_config_snapshot(cfg, "i"),
                    },
                    note=(
                        "anytime penny-longshot; "
                        f"{candidate_i['side']}@{candidate_i['entry']:.3f}; "
                        f"{'partial' if candidate_i['partial_fill'] else 'full'} fill "
                        f"{candidate_i['filled_shares']:.4f}/"
                        f"{candidate_i['requested_shares']:.4f} shares"
                    ),
                )

        if cfg["strategy_j_enabled"] and not self.has_trade("J", market_id):
            candidate_j = self.strategy_j_entry_candidate(snapshot, cfg)
            if candidate_j is not None:
                self.open_trade(
                    strategy="J",
                    topic_id=snapshot["topic_id"],
                    market_id=market_id,
                    side=candidate_j["side"],
                    entry=candidate_j["entry"],
                    target=candidate_j["target"],
                    stake=float(candidate_j["filled_stake"]),
                    fee_rate_bps=fee_bps,
                    strategy_version="J_v2_partial_fill",
                    diagnostics={
                        "execution_model": "top_ask_partial_fill_v1",
                        "signal_timestamp": snapshot["timestamp"],
                        "seconds_left": float(snapshot["seconds_left"]),
                        "quoted_ask": candidate_j["entry"],
                        "quoted_bid": candidate_j["bid"],
                        "target_price": candidate_j["target"],
                        "ask_gap": candidate_j["gap"],
                        "visible_ask_size": candidate_j["visible_size"],
                        "required_shares": candidate_j["required_shares"],
                        "requested_stake": candidate_j["requested_stake"],
                        "requested_shares": candidate_j["requested_shares"],
                        "filled_stake": candidate_j["filled_stake"],
                        "filled_shares": candidate_j["filled_shares"],
                        "fill_ratio": candidate_j["fill_ratio"],
                        "partial_fill": candidate_j["partial_fill"],
                        "book_skew_ms": candidate_j["book_skew_ms"],
                        "book_age_ms": candidate_j["book_age_ms"],
                        "config": strategy_config_snapshot(cfg, "j"),
                    },
                    note=(
                        "late-window dynamic target; "
                        f"{candidate_j['side']}@{candidate_j['entry']:.3f} -> "
                        f"{candidate_j['target']:.3f}; gap={candidate_j['gap']:.3f}; "
                        f"{'partial' if candidate_j['partial_fill'] else 'full'} fill "
                        f"{candidate_j['filled_shares']:.4f}/"
                        f"{candidate_j['requested_shares']:.4f} shares"
                    ),
                )

        for strategy, label in (("D", "time-arb"), ("F", "strict time-arb")):
            prefix = f"strategy_{strategy.lower()}"
            if not cfg[f"{prefix}_enabled"] or self.has_trade(strategy, market_id):
                continue
            valid = [(side, price) for side, price in asks.items() if price is not None]
            if len(valid) == 2:
                gap = abs(valid[0][1] - valid[1][1])
                side, entry = min(valid, key=lambda item: item[1])
                visible_size = snapshot.get(f"{side.lower()}_ask_size")
                required_shares = float(cfg[f"{prefix}_stake"]) / entry
                if (
                    elapsed <= float(cfg[f"{prefix}_window_seconds"])
                    and gap >= float(cfg[f"{prefix}_min_gap"])
                    and entry <= float(cfg[f"{prefix}_max_first_entry"])
                    and (visible_size is None or float(visible_size) >= required_shares)
                    and float(snapshot["seconds_left"]) > float(
                        cfg[f"{prefix}_force_exit_seconds"]
                    )
                ):
                    self.open_trade(
                        strategy=strategy, topic_id=snapshot["topic_id"], market_id=market_id,
                        side=side, entry=entry, target=None,
                        stake=float(cfg[f"{prefix}_stake"]), fee_rate_bps=fee_bps,
                        note=f"{label} first leg {side}@{entry:.3f}; gap={gap:.3f}",
                    )

    @staticmethod
    def _mx_exit_rule(strategy: str) -> str:
        if strategy in MX_FIXED_TARGETS:
            return f"all remaining shares at bid >= {MX_FIXED_TARGETS[strategy]:.2f}"
        variant = Store._mx_variant(strategy)
        if variant == "P50":
            return "50% at 1.5x entry; 50% at 2.0x entry"
        if variant == "P10":
            return "10% at each 1.1x through 2.0x entry"
        return "all remaining on Spot reverse-cross or held bid <= 0.50"

    def mx_state(
        self,
        strategy_ids: tuple[str, ...] = MX_STRATEGIES,
        *,
        signal_family: str = "M",
    ) -> dict[str, Any]:
        """Return the stable API contract for independent M exit branches."""
        terminal_positions = {
            "SETTLED_WIN", "SETTLED_LOSS", "EXPIRED_UNFILLED",
            "CLOSED_TARGET", "CLOSED_REVERSAL", "CLOSED_REVERSAL_NO_FILL",
        }
        strategies: dict[str, Any] = {}
        with self.lock:
            placeholders = ",".join("?" for _ in strategy_ids)
            all_positions = self.db.execute(
                f"""SELECT * FROM strategy_mx_positions
                    WHERE strategy IN ({placeholders})
                    ORDER BY created_at DESC, market_id DESC""",
                strategy_ids,
            ).fetchall()
            all_orders = self.db.execute(
                f"""SELECT * FROM strategy_mx_orders
                    WHERE strategy IN ({placeholders}) ORDER BY id DESC""",
                strategy_ids,
            ).fetchall()
            all_fills = self.db.execute(
                f"""SELECT * FROM strategy_mx_fills
                    WHERE strategy IN ({placeholders}) ORDER BY id DESC""",
                strategy_ids,
            ).fetchall()
            for strategy in strategy_ids:
                positions = [
                    row
                    for row in all_positions
                    if str(row["strategy"]) == strategy
                ]
                entry_orders = [
                    row
                    for row in all_orders
                    if str(row["strategy"]) == strategy
                    and row["action"] == "ENTRY"
                ]
                live_exit_orders = [
                    row
                    for row in all_orders
                    if str(row["strategy"]) == strategy
                    and row["action"] == "EXIT"
                    and row["stage_key"] != "SETTLEMENT"
                ]
                requested_entry = sum(
                    float(row["requested_entry_shares"]) for row in positions
                )
                filled_entry = sum(
                    float(row["filled_entry_shares"]) for row in positions
                )
                requested_exit = sum(
                    float(row["requested_shares"]) for row in live_exit_orders
                )
                filled_exit = sum(
                    float(row["filled_shares"]) for row in live_exit_orders
                )
                terminated_intents = [
                    row
                    for row in entry_orders
                    if row["status"] not in {"OPEN", "PARTIAL"}
                ]
                intent_count = len(terminated_intents)
                fully_unfilled = sum(
                    1
                    for row in terminated_intents
                    if float(row["filled_shares"]) <= 1e-12
                )
                partial_intents = sum(
                    1
                    for row in terminated_intents
                    if 1e-12 < float(row["filled_shares"])
                    < float(row["requested_shares"]) - 1e-12
                )
                settlement_exit = sum(
                    float(row["shares"])
                    for row in all_fills
                    if str(row["strategy"]) == strategy
                    and row["action"] == "EXIT"
                    and row["stage_key"] == "SETTLEMENT"
                )
                recent_positions = []
                for row in positions[:10]:
                    entry_shares = float(row["filled_entry_shares"])
                    recent_positions.append(
                        {
                            "experimentId": strategy,
                            "marketId": int(row["market_id"]),
                            "side": str(row["side"]),
                            "status": str(row["status"]),
                            "averageEntryPrice": (
                                float(row["entry_cost"]) / entry_shares
                                if entry_shares > 1e-12
                                else None
                            ),
                            "targetPrice": MX_FIXED_TARGETS.get(strategy),
                            "exitRule": self._mx_exit_rule(strategy),
                            "entryShares": entry_shares,
                            "remainingShares": float(row["remaining_shares"]),
                            "realizedPnl": float(row["realized_pnl"]),
                            "signalTimestamp": row["signal_timestamp"],
                            "createdAt": row["created_at"],
                            "closedAt": row["closed_at"],
                            "closeReason": row["close_reason"],
                        }
                    )
                order_rows = [
                    row
                    for row in all_orders
                    if str(row["strategy"]) == strategy
                ][:30]
                recent_orders = [
                    {
                        "id": int(row["id"]),
                        "experimentId": strategy,
                        "marketId": int(row["market_id"]),
                        "action": str(row["action"]),
                        "phase": str(row["stage_key"]),
                        "side": next(
                            (
                                str(position["side"])
                                for position in positions
                                if int(position["market_id"])
                                == int(row["market_id"])
                            ),
                            None,
                        ),
                        "limitPrice": (
                            float(row["limit_price"])
                            if row["limit_price"] is not None
                            else None
                        ),
                        "requestedQty": float(row["requested_shares"]),
                        "filledQty": float(row["filled_shares"]),
                        "remainingQty": max(
                            0.0,
                            float(row["requested_shares"])
                            - float(row["filled_shares"]),
                        ),
                        "status": str(row["status"]),
                        "reason": row["reason"],
                        "createdAt": row["created_at"],
                        "updatedAt": row["updated_at"],
                    }
                    for row in order_rows
                ]
                fill_rows = [
                    row
                    for row in all_fills
                    if str(row["strategy"]) == strategy
                ][:30]
                recent_fills = [
                    {
                        "id": int(row["id"]),
                        "experimentId": strategy,
                        "marketId": int(row["market_id"]),
                        "action": str(row["action"]),
                        "phase": str(row["stage_key"]),
                        "price": float(row["price"]),
                        "qty": float(row["shares"]),
                        "fee": float(row["fee"]),
                        "timestamp": str(row["timestamp"]),
                        "reason": row["reason"],
                    }
                    for row in fill_rows
                ]
                strategies[strategy] = {
                    "realizedPnl": sum(
                        float(row["realized_pnl"]) for row in positions
                    ),
                    "trades": sum(
                        1
                        for row in positions
                        if float(row["filled_entry_shares"]) > 1e-12
                    ),
                    "wins": sum(
                        1
                        for row in positions
                        if float(row["filled_entry_shares"]) > 1e-12
                        and str(row["status"]) in terminal_positions
                        and float(row["realized_pnl"]) > 0
                    ),
                    "losses": sum(
                        1
                        for row in positions
                        if float(row["filled_entry_shares"]) > 1e-12
                        and str(row["status"]) in terminal_positions
                        and float(row["realized_pnl"]) <= 0
                    ),
                    "open": sum(
                        1
                        for row in positions
                        if str(row["status"]) not in terminal_positions
                    ),
                    "requestedEntryShares": requested_entry,
                    "filledEntryShares": filled_entry,
                    "requestedExitShares": requested_exit,
                    "filledExitShares": filled_exit,
                    "settlementExitShares": settlement_exit,
                    "remainingShares": sum(
                        float(row["remaining_shares"]) for row in positions
                    ),
                    "entryFillRatio": (
                        min(1.0, filled_entry / requested_entry)
                        if requested_entry > 0
                        else 0.0
                    ),
                    "exitFillRatio": (
                        min(1.0, filled_exit / requested_exit)
                        if requested_exit > 0
                        else 0.0
                    ),
                    "fullyUnfilledIntentRatio": (
                        fully_unfilled / intent_count if intent_count else 0.0
                    ),
                    "partialFillIntentRatio": (
                        partial_intents / intent_count if intent_count else 0.0
                    ),
                    "recentPositions": recent_positions,
                    "recentOrders": recent_orders,
                    "recentFills": recent_fills,
                }
        summaries = {
            strategy: {
                "realizedPnl": values["realizedPnl"],
                "trades": values["trades"],
                "wins": values["wins"],
                "losses": values["losses"],
                "openPositions": values["open"],
                "requestedEntryQty": values["requestedEntryShares"],
                "filledEntryQty": values["filledEntryShares"],
                "requestedExitQty": values["requestedExitShares"],
                "filledExitQty": values["filledExitShares"],
                "settlementExitQty": values["settlementExitShares"],
                "remainingShares": values["remainingShares"],
                "entryFillRatio": values["entryFillRatio"],
                "exitFillRatio": values["exitFillRatio"],
                "fullyUnfilledIntentRatio": values[
                    "fullyUnfilledIntentRatio"
                ],
                "partialFillIntentRatio": values[
                    "partialFillIntentRatio"
                ],
            }
            for strategy, values in strategies.items()
        }
        positions = sorted(
            (
                position
                for values in strategies.values()
                for position in values["recentPositions"]
                if float(position["entryShares"]) > 1e-12
                and str(position["status"]) not in terminal_positions
            ),
            key=lambda position: str(position.get("createdAt") or ""),
            reverse=True,
        )[:100]
        orders = sorted(
            (
                order
                for values in strategies.values()
                for order in values["recentOrders"]
            ),
            key=lambda order: int(order["id"]),
            reverse=True,
        )[:100]
        fills = sorted(
            (
                fill
                for values in strategies.values()
                for fill in values["recentFills"]
            ),
            key=lambda fill: int(fill["id"]),
            reverse=True,
        )[:100]
        return {
            "contractVersion": 2,
            "ledgerVersion": MX_LEDGER_VERSION,
            "status": "LIVE",
            "updatedAt": utc_iso(),
            "entryLimit": MX_ENTRY_LIMIT,
            "entryWindowSeconds": float(
                self.config()["strategy_m_entry_window_seconds"]
            ),
            "signalFamily": signal_family,
            "strategyIds": list(strategy_ids),
            "summaries": summaries,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            # Kept for local analysis scripts that consumed the first internal
            # contract while this experiment was under development.
            "strategies": strategies,
        }

    def pair_arb_state(self) -> dict[str, Any]:
        """Return the dedicated paper ledger for complementary pair tests."""
        cfg = self.config()
        summaries: dict[str, Any] = {}
        recent: list[dict[str, Any]] = []
        with self.lock:
            stats = self.db.execute(
                """SELECT COUNT(*) AS markets_evaluated,
                          COALESCE(SUM(evaluations),0) AS evaluations,
                          COALESCE(SUM(valid_book_evaluations),0) AS valid_books,
                          COALESCE(SUM(eligible_010_snapshots),0) AS eligible_010,
                          COALESCE(SUM(eligible_qc_015_snapshots),0) AS eligible_qc_015,
                          COALESCE(SUM(eligible_020_snapshots),0) AS eligible_020,
                          COALESCE(SUM(rejected_invalid_book),0) AS invalid_book,
                          COALESCE(SUM(rejected_book_skew),0) AS book_skew,
                          COALESCE(SUM(rejected_book_age),0) AS book_age,
                          COALESCE(SUM(rejected_edge),0) AS edge,
                          MAX(best_net_edge) AS best_net_edge,
                          COUNT(CASE WHEN eligible_010_snapshots > 0 THEN 1 END)
                              AS eligible_010_markets,
                          COUNT(CASE WHEN eligible_qc_015_snapshots > 0 THEN 1 END)
                              AS eligible_qc_015_markets,
                          COUNT(CASE WHEN eligible_020_snapshots > 0 THEN 1 END)
                              AS eligible_020_markets
                   FROM strategy_pair_arb_market_stats"""
            ).fetchone()
            latest_stat = self.db.execute(
                """SELECT * FROM strategy_pair_arb_market_stats
                   ORDER BY last_evaluated_at DESC LIMIT 1"""
            ).fetchone()
            for strategy, minimum_edge in PAIR_ARB_THRESHOLDS.items():
                rows = self.db.execute(
                    """SELECT * FROM strategy_pair_arb_trades
                       WHERE strategy=? ORDER BY id ASC""",
                    (strategy,),
                ).fetchall()
                total_cost = sum(float(row["total_cost"]) for row in rows)
                locked_pnl = sum(float(row["locked_pnl"]) for row in rows)
                cumulative = 0.0
                peak = 0.0
                max_drawdown = 0.0
                for row in rows:
                    cumulative += float(row["locked_pnl"])
                    peak = max(peak, cumulative)
                    max_drawdown = max(max_drawdown, peak - cumulative)
                count = len(rows)
                depth_rows = [
                    row for row in rows if float(row["requested_shares"] or 0) > 0
                ]
                requested_quantity = sum(
                    float(row["requested_shares"]) for row in depth_rows
                )
                filled_quantity = sum(float(row["shares"]) for row in depth_rows)
                eligible_markets = int(
                    self.db.execute(
                        """SELECT COUNT(*)
                           FROM strategy_pair_arb_market_stats
                           WHERE best_net_edge IS NOT NULL
                             AND best_net_edge + 0.000000000001 >= ?""",
                        (minimum_edge,),
                    ).fetchone()[0]
                )
                summaries[strategy] = {
                    "strategyId": strategy,
                    "enabled": bool(
                        cfg[f"strategy_{strategy.lower()}_enabled"]
                    ),
                    "minimumNetEdge": minimum_edge,
                    "trades": count,
                    "lockedPnl": locked_pnl,
                    "totalCost": total_cost,
                    "roi": locked_pnl / total_cost if total_cost else None,
                    "stressedPnl005": sum(
                        float(row["stressed_pnl_005"]) for row in rows
                    ),
                    "stressedPnl010": sum(
                        float(row["stressed_pnl_010"]) for row in rows
                    ),
                    "totalShares": sum(float(row["shares"]) for row in rows),
                    "averageNetEdge": (
                        sum(float(row["net_edge_per_share"]) for row in rows)
                        / count if count else None
                    ),
                    "averageSecondsLeft": (
                        sum(float(row["seconds_left"]) for row in rows) / count
                        if count else None
                    ),
                    "averageBookSkewMs": (
                        sum(float(row["book_skew_ms"]) for row in rows) / count
                        if count else None
                    ),
                    "averageBookAgeMs": (
                        sum(float(row["book_age_ms"]) for row in rows) / count
                        if count else None
                    ),
                    "maxDrawdown": max_drawdown,
                    "eligibleMarkets": eligible_markets,
                    "pairFillRate": (
                        count / eligible_markets if eligible_markets else None
                    ),
                    "quantityFillRate": (
                        filled_quantity / requested_quantity
                        if requested_quantity else None
                    ),
                    "requestedShares": requested_quantity,
                    "filledShares": filled_quantity,
                    "partialFills": sum(
                        int(bool(row["partial_fill"])) for row in depth_rows
                    ),
                }
                recent.extend(dict(row) for row in rows[-50:])
            diagnostics = {
                "source": "independent_outcome_books",
                "transport": "concurrent_signed_rest",
                "evaluations": int(stats["evaluations"]),
                "marketsEvaluated": int(stats["markets_evaluated"]),
                "validBookEvaluations": int(stats["valid_books"]),
                "bestNetEdge": (
                    float(stats["best_net_edge"])
                    if stats["best_net_edge"] is not None else None
                ),
                "eligibleSnapshots": {
                    "PAIR_ARB_010": int(stats["eligible_010"]),
                    "PAIR_ARB_QC_015": int(stats["eligible_qc_015"]),
                    "PAIR_ARB_020": int(stats["eligible_020"]),
                },
                "rejectionCounts": {
                    "invalidBook": int(stats["invalid_book"]),
                    "bookSkewExceeded": int(stats["book_skew"]),
                    "bookAgeExceeded": int(stats["book_age"]),
                    "edgeBelow010": int(stats["edge"]),
                },
                "latestMarket": (
                    {
                        "marketId": int(latest_stat["market_id"]),
                        "evaluatedAt": str(latest_stat["last_evaluated_at"]),
                        "reason": str(latest_stat["last_reason"]),
                        "netEdge": (
                            float(latest_stat["last_net_edge"])
                            if latest_stat["last_net_edge"] is not None else None
                        ),
                        "upAsk": (
                            float(latest_stat["last_up_ask"])
                            if latest_stat["last_up_ask"] is not None else None
                        ),
                        "downAsk": (
                            float(latest_stat["last_down_ask"])
                            if latest_stat["last_down_ask"] is not None else None
                        ),
                        "bookSkewMs": (
                            float(latest_stat["last_book_skew_ms"])
                            if latest_stat["last_book_skew_ms"] is not None else None
                        ),
                        "bookAgeMs": (
                            float(latest_stat["last_book_age_ms"])
                            if latest_stat["last_book_age_ms"] is not None else None
                        ),
                    }
                    if latest_stat is not None else None
                ),
            }
        recent.sort(key=lambda row: int(row["id"]), reverse=True)
        return {
            "status": "LIVE",
            "updatedAt": utc_iso(),
            "paperOnly": True,
            "fillModel": "dual_token_orderbook_depth_vwap_partial_fill_v1",
            "atomicExecutionAssumed": False,
            "sharedStakeUsdt": float(cfg["strategy_pair_arb_stake"]),
            "maxBookSkewMs": float(
                cfg["strategy_pair_arb_max_book_skew_ms"]
            ),
            "maxBookAgeMs": float(cfg["strategy_pair_arb_max_book_age_ms"]),
            "summaries": summaries,
            "diagnostics": diagnostics,
            "recentTrades": recent[:100],
        }

    def futures_lead_observer_trade_page(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Read the full persisted Observer strategy ledger by page."""
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(100, int(page_size)))
        observer_strategies = (
            *FUTURES_LEAD_OBSERVER_STRATEGIES,
            *OBSERVER_COMBINATION_STRATEGIES,
            *OBSERVER_AUTO_V6_STRATEGIES,
        )
        placeholders = ",".join("?" for _ in observer_strategies)
        with self.lock:
            total = int(
                self.db.execute(
                    f"SELECT COUNT(*) FROM trades WHERE strategy IN ({placeholders})",
                    observer_strategies,
                ).fetchone()[0]
            )
            total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
            active_page = min(safe_page, total_pages)
            offset = (active_page - 1) * safe_page_size
            rows = self.db.execute(
                f"""SELECT * FROM trades
                     WHERE strategy IN ({placeholders})
                     ORDER BY id DESC LIMIT ? OFFSET ?""",
                (*observer_strategies, safe_page_size, offset),
            ).fetchall()
        return {
            "scope": "OBSERVER_STRATEGIES",
            "storage": "SQLITE_FULL_HISTORY",
            "strategies": list(observer_strategies),
            "trades": [dict(row) for row in rows],
            "page": active_page,
            "pageSize": safe_page_size,
            "total": total,
            "totalPages": total_pages,
        }

    def dashboard(
        self,
        collector: "Collector",
        *,
        include_experiments: bool = True,
    ) -> dict[str, Any]:
        with self.lock:
            latest = self.db.execute("SELECT * FROM observations ORDER BY id DESC LIMIT 1").fetchone()
            history = self.db.execute(
                "SELECT * FROM observations ORDER BY id DESC LIMIT 90"
            ).fetchall()[::-1]
            trades = self.db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50").fetchall()
            reset_rows = self.db.execute(
                """SELECT strategy, cutoff_trade_id, reset_at
                   FROM strategy_measurement_resets ORDER BY id ASC"""
            ).fetchall()
            latest_reset_by_strategy = {
                str(row["strategy"]): {
                    "resetAt": str(row["reset_at"]),
                    "cutoffTradeId": int(row["cutoff_trade_id"]),
                }
                for row in reset_rows
            }
            summaries = {}
            for strategy in SUPPORTED_STRATEGIES:
                reset = latest_reset_by_strategy.get(strategy)
                cutoff_trade_id = int(reset["cutoffTradeId"]) if reset else 0
                row = self.db.execute(
                    """SELECT COUNT(*) trades,
                       COALESCE(SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END), 0) open,
                       COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) wins,
                       COALESCE(SUM(CASE
                           WHEN pnl <= 0 AND pnl IS NOT NULL THEN 1 ELSE 0 END), 0) losses,
                       COALESCE(SUM(pnl), 0) realized_pnl
                       FROM trades WHERE strategy=? AND id>?""",
                    (strategy, cutoff_trade_id),
                ).fetchone()
                carried_open = 0
                if reset is not None:
                    carried_open = int(
                        self.db.execute(
                            """SELECT COUNT(*) FROM trades
                               WHERE strategy=? AND id<=? AND status='OPEN'""",
                            (strategy, cutoff_trade_id),
                        ).fetchone()[0]
                    )
                summary = dict(row)
                summary["resetAt"] = reset["resetAt"] if reset else None
                summary["cutoffTradeId"] = cutoff_trade_id if reset else None
                summary["carriedOpen"] = carried_open
                summary["totalOpen"] = int(summary["open"]) + carried_open
                if strategy == "M0":
                    outcomes = [
                        float(result["pnl"]) > 0
                        for result in self.db.execute(
                            """SELECT pnl FROM trades
                               WHERE strategy='M0' AND id>? AND pnl IS NOT NULL
                               ORDER BY id ASC""",
                            (cutoff_trade_id,),
                        ).fetchall()
                    ]
                    win_runs: list[int] = []
                    loss_runs: list[int] = []
                    if outcomes:
                        current_outcome = outcomes[0]
                        current_length = 1
                        for outcome in outcomes[1:]:
                            if outcome == current_outcome:
                                current_length += 1
                            else:
                                (win_runs if current_outcome else loss_runs).append(
                                    current_length
                                )
                                current_outcome = outcome
                                current_length = 1
                        (win_runs if current_outcome else loss_runs).append(
                            current_length
                        )
                    summary["currentWinStreak"] = (
                        win_runs[-1] if outcomes and outcomes[-1] else 0
                    )
                    summary["currentLossStreak"] = (
                        loss_runs[-1] if outcomes and not outcomes[-1] else 0
                    )
                    summary["averageWinStreak"] = (
                        sum(win_runs) / len(win_runs) if win_runs else 0.0
                    )
                    summary["averageLossStreak"] = (
                        sum(loss_runs) / len(loss_runs) if loss_runs else 0.0
                    )
                    # Project M0 through M0W's gate: M0W observes a round only
                    # when the preceding M0 result was a win. Loss runs in this
                    # projected sequence are M0W's theoretical loss streaks.
                    m0w_projected_outcomes = [
                        outcomes[index]
                        for index in range(1, len(outcomes))
                        if outcomes[index - 1]
                    ]
                    m0w_loss_runs: list[int] = []
                    current_m0w_loss_run = 0
                    for outcome in m0w_projected_outcomes:
                        if not outcome:
                            current_m0w_loss_run += 1
                        elif current_m0w_loss_run:
                            m0w_loss_runs.append(current_m0w_loss_run)
                            current_m0w_loss_run = 0
                    if current_m0w_loss_run:
                        m0w_loss_runs.append(current_m0w_loss_run)
                    summary["averageWinLossCycleStreak"] = (
                        sum(m0w_loss_runs) / len(m0w_loss_runs)
                        if m0w_loss_runs else 0.0
                    )
                summaries[strategy] = summary
            m0_reset = latest_reset_by_strategy.get("M0")
            m0_cutoff_trade_id = (
                int(m0_reset["cutoffTradeId"]) if m0_reset else 0
            )
            m0_result_rows = self.db.execute(
                """SELECT id, market_id, opened_at, status,
                          strftime('%Y-%m-%d', opened_at, '+8 hours') local_date,
                          CAST(strftime('%H', opened_at, '+8 hours') AS INTEGER) hour
                     FROM trades
                    WHERE strategy='M0' AND id>?
                      AND status IN ('SETTLED_WIN','SETTLED_LOSS')
                    ORDER BY opened_at ASC, id ASC""",
                (m0_cutoff_trade_id,),
            ).fetchall()
            m0_by_hour: dict[int, dict[str, Any]] = {
                hour: {
                    "settled": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_runs": [],
                    "loss_runs": [],
                    "win_then_loss_count": 0,
                    "win_then_loss_opportunities": 0,
                }
                for hour in range(24)
            }
            m0_hour_segments: dict[tuple[str, int], list[dict[str, Any]]] = {}
            ordered_m0_results: list[dict[str, Any]] = []
            for row in m0_result_rows:
                hour = int(row["hour"])
                outcome = str(row["status"]) == "SETTLED_WIN"
                opened_at = datetime.fromisoformat(
                    str(row["opened_at"]).replace("Z", "+00:00")
                ).timestamp()
                result = {
                    "hour": hour,
                    "local_date": str(row["local_date"]),
                    "opened_at": opened_at,
                    "outcome": outcome,
                }
                stats = m0_by_hour[hour]
                stats["settled"] += 1
                stats["wins" if outcome else "losses"] += 1
                ordered_m0_results.append(result)
                m0_hour_segments.setdefault(
                    (result["local_date"], hour), []
                ).append(result)

            def adjacent_m0_results(
                previous: dict[str, Any], current: dict[str, Any]
            ) -> bool:
                gap_seconds = float(current["opened_at"]) - float(
                    previous["opened_at"]
                )
                return abs(gap_seconds - (M_MARKET_DURATION_MS / 1000.0)) <= 60.0

            # Average streaks are local to one Taipei calendar hour. A new
            # date, hour, or missing five-minute market starts a new run so
            # unrelated observations are never stitched together.
            for (_, hour), segment in m0_hour_segments.items():
                current_outcome: bool | None = None
                current_length = 0
                previous_result: dict[str, Any] | None = None
                for result in segment:
                    continuous = bool(
                        previous_result is not None
                        and adjacent_m0_results(previous_result, result)
                    )
                    outcome = bool(result["outcome"])
                    if not continuous or outcome != current_outcome:
                        if current_outcome is not None and current_length:
                            m0_by_hour[hour][
                                "win_runs" if current_outcome else "loss_runs"
                            ].append(current_length)
                        current_outcome = outcome
                        current_length = 1
                    else:
                        current_length += 1
                    previous_result = result
                if current_outcome is not None and current_length:
                    m0_by_hour[hour][
                        "win_runs" if current_outcome else "loss_runs"
                    ].append(current_length)

            # Bucket an adjacent W->L transition by the current round's hour.
            # Among rounds allowed by M0W's previous-win gate, this is the
            # direction-level theoretical M0W loss rate for that entry hour.
            for previous, current in zip(
                ordered_m0_results, ordered_m0_results[1:]
            ):
                if not adjacent_m0_results(previous, current):
                    continue
                if bool(previous["outcome"]):
                    stats = m0_by_hour[int(current["hour"])]
                    stats["win_then_loss_opportunities"] += 1
                    if not bool(current["outcome"]):
                        stats["win_then_loss_count"] += 1

            m0_hours = []
            for hour in range(24):
                stats = m0_by_hour[hour]
                settled = int(stats["settled"])
                wins = int(stats["wins"])
                losses = int(stats["losses"])
                win_runs = stats["win_runs"]
                loss_runs = stats["loss_runs"]
                win_then_loss_count = int(stats["win_then_loss_count"])
                win_then_loss_opportunities = int(
                    stats["win_then_loss_opportunities"]
                )
                m0_hours.append(
                    {
                        "hour": hour,
                        "label": f"{hour:02d}:00–{hour:02d}:59",
                        "settledTrades": settled,
                        "wins": wins,
                        "losses": losses,
                        "winRatePct": (
                            (wins / settled) * 100.0 if settled else None
                        ),
                        "averageWinStreak": (
                            sum(win_runs) / len(win_runs) if win_runs else None
                        ),
                        "averageLossStreak": (
                            sum(loss_runs) / len(loss_runs) if loss_runs else None
                        ),
                        "winThenLossCount": win_then_loss_count,
                        "winThenLossOpportunities": win_then_loss_opportunities,
                        "winThenLossRatePct": (
                            (win_then_loss_count / win_then_loss_opportunities)
                            * 100.0
                            if win_then_loss_opportunities else None
                        ),
                    }
                )
            m0_range = self.db.execute(
                """SELECT MIN(opened_at) first_opened_at,
                          MAX(opened_at) last_opened_at
                     FROM trades
                    WHERE strategy='M0' AND id>?
                      AND status IN ('SETTLED_WIN','SETTLED_LOSS')""",
                (m0_cutoff_trade_id,),
            ).fetchone()
            m0_hourly_performance = {
                "timezone": "Asia/Taipei",
                "utcOffset": "+08:00",
                "basis": "M0 opened_at; settled trades only",
                "streakBasis": (
                    "Taipei local date/hour segments; non-adjacent five-minute "
                    "markets break streaks"
                ),
                "winThenLossBasis": (
                    "current-hour result conditional on an adjacent previous "
                    "M0 win; direction-level theoretical M0W loss rate"
                ),
                "resetAt": m0_reset["resetAt"] if m0_reset else None,
                "cutoffTradeId": m0_cutoff_trade_id if m0_reset else None,
                "firstOpenedAt": m0_range["first_opened_at"],
                "lastOpenedAt": m0_range["last_opened_at"],
                "settledTrades": sum(
                    int(bucket["settledTrades"]) for bucket in m0_hours
                ),
                "hours": m0_hours,
            }
        dashboard_trades: list[dict[str, Any]] = []
        timing_keys = (
            "signal_timestamp",
            "execution_timestamp",
            "requested_delay_seconds",
            "actual_delay_seconds",
            "execution_lag_seconds",
        )
        for row in trades:
            item = dict(row)
            raw_diagnostics = item.pop("diagnostics_json", None)
            if str(item.get("strategy") or "").startswith("M7_") and raw_diagnostics:
                try:
                    parsed = json.loads(str(raw_diagnostics))
                    if isinstance(parsed, dict):
                        item["diagnostics"] = {
                            key: parsed.get(key)
                            for key in timing_keys
                            if parsed.get(key) is not None
                        }
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            dashboard_trades.append(item)
        research_config = self.config()
        research_exposure = self._research_open_exposure()
        research_shadow_exposure = self._research_shadow_open_exposure()
        research_cap = float(research_config["strategy_research_shared_cap_usdt"])
        regime_direction_control, _ = self._futures_lead_regime_direction_control(
            research_config
        )
        research_forward = {
            "status": "PAPER_ONLY",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "confirmationAdd": self.confirmation_add_shadow_summary(),
            "strategies": {
                strategy: {
                    "enabled": bool(
                        research_config[f"strategy_{strategy.lower()}_enabled"]
                    ),
                    "stakeUsdt": float(
                        research_config[f"strategy_{strategy.lower()}_stake"]
                    ),
                    "selectedBacktestParameters": RESEARCH_PARAMETERS[strategy],
                    "chronologicalValidation": (
                        self._research_experiment_validation_state(strategy)
                        if strategy in {
                            *CONTINUOUS_CALIBRATION_STRATEGIES,
                            *FUTURES_LEAD_EXPERIMENT_STRATEGIES,
                            *FUTURES_LEAD_OBSERVER_STRATEGIES,
                            *OBSERVER_COMBINATION_STRATEGIES,
                            *OBSERVER_AUTO_V6_STRATEGIES,
                        }
                        else None
                    ),
                    **(
                        {
                            "continuousCalibration": (
                                self._research_continuous_calibration_state(strategy)
                            )
                        }
                        if strategy in CONTINUOUS_CALIBRATION_STRATEGIES
                        else {}
                    ),
                    **(
                        {
                            "observerAutoV6": (
                                self._research_observer_auto_v6_state(strategy)
                            )
                        }
                        if strategy in OBSERVER_AUTO_V6_STRATEGIES
                        else {}
                    ),
                    **(
                        {"directionControl": regime_direction_control}
                        if strategy == "R_FUTURES_LEAD_REGIME_REVERSE_3L"
                        else {}
                    ),
                }
                for strategy in RESEARCH_STRATEGIES
            },
            "minimumStakeUsdt": float(
                research_config["strategy_research_min_stake_usdt"]
            ),
            "minimumBasis": (
                "local live executor records an approximately 1.5 USDT MARKET "
                "minimum; paper safety floor is 2 USDT"
            ),
            "sharedCapitalCapUsdt": research_cap,
            "openExposureUsdt": research_exposure,
            "availableExposureUsdt": max(0.0, research_cap - research_exposure),
            "shadowOpenExposureUsdt": research_shadow_exposure,
            "shadowCapitalModel": "isolated counterfactual; excluded from primary shared cap and depth reservations",
            "execution": {
                "actualPredictionTopOfBook": True,
                "fullFirstLevelDepthRequired": True,
                "partialFillsAllowed": False,
                "slippageBps": float(
                    research_config["strategy_research_slippage_bps"]
                ),
                "maxSpread": float(
                    research_config["strategy_research_max_spread"]
                ),
                "maxBookAgeMs": float(
                    research_config["strategy_research_max_book_age_ms"]
                ),
                "maxBookSkewMs": float(
                    research_config["strategy_research_max_book_skew_ms"]
                ),
            },
        }
        return {
            "connection": {
                "status": collector.status,
                "error": collector.error,
                "updatedAt": collector.updated_at,
                "intervalSeconds": collector.interval,
                "rateLimits": collector.prediction.last_rate_limits if collector.prediction else {},
            },
            "config": self.config(),
            "latest": dict(latest) if latest else None,
            "history": [dict(row) for row in history],
            "trades": dashboard_trades,
            "summaries": summaries,
            "researchForward": research_forward,
            "m0HourlyPerformance": m0_hourly_performance,
            "m01oFilterExperiment": self.m01o_filter_experiment_state(),
            "strategyH": self.strategy_h_state(),
            "mExitExperiment": self.mx_state() if include_experiments else None,
            "m0ExitExperiment": (
                self.mx_state(M0X_STRATEGIES, signal_family="M0")
                if include_experiments
                else None
            ),
            "pairArbExperiment": (
                self.pair_arb_state() if include_experiments else None
            ),
        }

    def m0_hourly_guard_snapshot(self) -> dict[str, Any]:
        """Read only the settled M0 fields required by the live hourly guard."""
        with self.lock:
            reset = self.db.execute(
                """SELECT cutoff_trade_id, reset_at
                     FROM strategy_measurement_resets
                    WHERE strategy='M0' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            cutoff_trade_id = int(reset["cutoff_trade_id"]) if reset else 0
            rows = self.db.execute(
                """SELECT id, opened_at, status,
                          CAST(strftime('%H', opened_at, '+8 hours') AS INTEGER) hour
                     FROM trades
                    WHERE strategy='M0' AND id>?
                      AND status IN ('SETTLED_WIN','SETTLED_LOSS')
                    ORDER BY opened_at ASC, id ASC""",
                (cutoff_trade_id,),
            ).fetchall()

        buckets = {
            hour: {
                "settled": 0,
                "wins": 0,
                "losses": 0,
                "win_then_loss_count": 0,
                "win_then_loss_opportunities": 0,
            }
            for hour in range(24)
        }
        ordered: list[tuple[float, int, bool]] = []
        for row in rows:
            hour = int(row["hour"])
            won = str(row["status"]) == "SETTLED_WIN"
            bucket = buckets[hour]
            bucket["settled"] += 1
            bucket["wins" if won else "losses"] += 1
            opened_at = datetime.fromisoformat(
                str(row["opened_at"]).replace("Z", "+00:00")
            ).timestamp()
            ordered.append((opened_at, hour, won))

        for previous, current in zip(ordered, ordered[1:]):
            gap_seconds = current[0] - previous[0]
            if abs(gap_seconds - (M_MARKET_DURATION_MS / 1000.0)) > 60.0:
                continue
            if previous[2]:
                bucket = buckets[current[1]]
                bucket["win_then_loss_opportunities"] += 1
                if not current[2]:
                    bucket["win_then_loss_count"] += 1

        hours = []
        for hour in range(24):
            bucket = buckets[hour]
            settled = int(bucket["settled"])
            wins = int(bucket["wins"])
            opportunities = int(bucket["win_then_loss_opportunities"])
            transitions = int(bucket["win_then_loss_count"])
            hours.append(
                {
                    "hour": hour,
                    "label": f"{hour:02d}:00–{hour:02d}:59",
                    "settledTrades": settled,
                    "wins": wins,
                    "losses": int(bucket["losses"]),
                    "winRatePct": (wins / settled) * 100.0 if settled else None,
                    "winThenLossCount": transitions,
                    "winThenLossOpportunities": opportunities,
                    "winThenLossRatePct": (
                        (transitions / opportunities) * 100.0
                        if opportunities else None
                    ),
                }
            )
        return {
            "timezone": "Asia/Taipei",
            "utcOffset": "+08:00",
            "basis": "M0 opened_at; settled trades only",
            "resetAt": str(reset["reset_at"]) if reset else None,
            "cutoffTradeId": cutoff_trade_id if reset else None,
            "settledTrades": len(rows),
            "hours": hours,
        }


class Collector(threading.Thread):
    def __init__(self, store: Store, api_key: str | None, api_secret: str | None) -> None:
        super().__init__(daemon=True)
        self.store = store
        self.status = "STARTING" if api_key and api_secret else "CONFIG_REQUIRED"
        self.error: str | None = None
        self.updated_at: str | None = None
        self.prediction = BinancePredictionClient(api_key, api_secret) if api_key and api_secret else None
        self.spot = BinanceClient()
        self.market: dict[str, Any] | None = None
        self.last_spot: float | None = None
        self.latest_snapshot: dict[str, Any] | None = None
        self.stop_event = threading.Event()
        self.interval = COLLECT_INTERVAL_SECONDS
        self.next_settlement_recheck = 0.0
        self.pending_rollover_settlements: deque[
            tuple[dict[str, Any], float]
        ] = deque()
        # Slow REST calls never hold this lock.  It only makes the supervisor's
        # market publication and the collector's final validation/Store commit
        # one ordered operation, closing the post-validation rollover race.
        self.market_lock = threading.RLock()
        self.pending_settlement_lock = threading.Lock()
        self.market_watch_thread: threading.Thread | None = None
        self.live_signal_sink: Callable[[dict[str, Any]], None] | None = None
        self.realtime_prediction_sink: Callable[[dict[str, Any]], None] | None = None
        self.confirmation_snapshot_sink: Callable[[dict[str, Any]], None] | None = None

    def settle_previous(
        self,
        market: dict[str, Any] | None = None,
        fallback_spot: float | None = None,
    ) -> None:
        market = market or self.market
        fallback_spot = self.last_spot if fallback_spot is None else fallback_spot
        if not market or fallback_spot is None or not self.prediction:
            return
        # Settlement must never delay discovery of the next five-minute market.
        # If the detail request is unavailable, record the saved pre-rollover
        # Spot proxy now and let reconcile_pending_settlement upgrade it later.
        try:
            detail = self.prediction.market_detail(int(market["marketTopicId"]))
            end_raw = detail.get("variantData", {}).get("endPrice")
        except Exception:
            end_raw = None
        start_price = float(market["variantData"]["startPrice"])
        end_price = float(end_raw) if end_raw is not None else float(fallback_spot)
        self.store.settle_market(
            int(market["_selectedMarket"]["market"]["marketId"]),
            "UP" if end_price > start_price else "DOWN",
            end_raw is not None,
            topic_id=int(market["marketTopicId"]),
            start_price=start_price,
            end_price=float(end_raw) if end_raw is not None else None,
        )

    def reconcile_pending_settlement(self) -> None:
        """Upgrade the oldest proxy settlement once Binance publishes endPrice."""
        if not self.prediction:
            return
        pending = self.store.next_pending_settlement()
        if pending is None:
            return
        detail = self.prediction.market_detail(int(pending["topic_id"]))
        self.store.mark_settlement_checked(int(pending["market_id"]))
        end_raw = detail.get("variantData", {}).get("endPrice")
        if end_raw is None:
            return
        start_price = float(pending["start_price"])
        end_price = float(end_raw)
        self.store.settle_market(
            int(pending["market_id"]),
            "UP" if end_price > start_price else "DOWN",
            True,
            topic_id=int(pending["topic_id"]),
            start_price=start_price,
            end_price=end_price,
        )

    def reconcile_untracked_open_settlement(
        self, exclude_market_id: int | None = None
    ) -> bool:
        """Officially settle one orphan OPEN market that survived a restart."""
        if not self.prediction:
            return False
        pending = self.store.next_untracked_open_settlement(exclude_market_id)
        if pending is None:
            return False
        detail = self.prediction.market_detail(int(pending["topic_id"]))
        end_raw = detail.get("variantData", {}).get("endPrice")
        if end_raw is None:
            return False
        start_price = float(pending["start_price"])
        end_price = float(end_raw)
        self.store.settle_market(
            int(pending["market_id"]),
            "UP" if end_price > start_price else "DOWN",
            True,
            topic_id=int(pending["topic_id"]),
            start_price=start_price,
            end_price=end_price,
        )
        return True

    def _is_current_market(self, market: dict[str, Any]) -> bool:
        with self.market_lock:
            current = self.market
            try:
                return bool(
                    current
                    and int(current["marketTopicId"])
                    == int(market["marketTopicId"])
                )
            except (KeyError, TypeError, ValueError):
                return False

    def _market_rollover_loop(self) -> None:
        """Publish new market metadata independently of slow A-L REST polls.

        A Spot/order-book request can legally remain blocked until its HTTP
        timeout.  Keeping boundary discovery on this small supervisor means a
        stale old-market request can never hold the M-series market ID hostage.
        The collector validates its captured topic again after every slow call.
        """
        assert self.prediction is not None
        while not self.stop_event.is_set():
            wait_seconds = 0.05
            try:
                now_ms = self.prediction.server_timestamp_ms()
                with self.market_lock:
                    market = self.market
                    if market is not None and int(market["endDate"]) <= now_ms:
                        if self.last_spot is not None:
                            topic_id = int(market["marketTopicId"])
                            with self.pending_settlement_lock:
                                if not any(
                                    int(item[0]["marketTopicId"]) == topic_id
                                    for item in self.pending_rollover_settlements
                                ):
                                    self.pending_rollover_settlements.append(
                                        (market, float(self.last_spot))
                                    )
                        self.market = None
                    needs_discovery = self.market is None

                if needs_discovery:
                    discovered = self.prediction.find_market_summary(
                        "BTCUSDT",
                        now=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc),
                        # The lookup stops as soon as page 0 contains BTC, so
                        # normal rollover remains one request.  Additional
                        # pages only prevent permanent starvation if Binance's
                        # END_DATE ordering moves BTC beyond the first 100 rows.
                        max_pages=5,
                    )
                    # The request itself may have crossed a boundary.  Refuse
                    # to publish a list record that is already expired under
                    # the same Binance-adjusted clock.
                    checked_ms = self.prediction.server_timestamp_ms()
                    with self.market_lock:
                        if (
                            self.market is None
                            and discovered
                            and int(discovered["endDate"]) > checked_ms
                        ):
                            self.market = discovered
                        market = self.market
                    if market is None:
                        self.status = "WAITING_FOR_MARKET"
                        wait_seconds = 0.05
                else:
                    with self.market_lock:
                        market = self.market

                if market is not None:
                    remaining_ms = int(market["endDate"]) - self.prediction.server_timestamp_ms()
                    if remaining_ms > 0:
                        wait_seconds = min(0.05, max(0.005, remaining_ms / 1000))
            except Exception as exc:
                self.status = "RATE_LIMITED" if "HTTP 429" in str(exc) else "ERROR"
                self.error = str(exc)[:300]
                if self.status == "RATE_LIMITED":
                    wait_seconds = max(
                        10.0, self.prediction.retry_after_seconds or 0.0
                    )
                else:
                    wait_seconds = 0.25
            self.stop_event.wait(wait_seconds)

    def run(self) -> None:
        if not self.prediction:
            return
        self.market_watch_thread = threading.Thread(
            target=self._market_rollover_loop,
            name="prediction-market-rollover",
            daemon=True,
        )
        self.market_watch_thread.start()
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            next_delay = self.interval
            try:
                with self.market_lock:
                    market = self.market
                if market is None:
                    self.status = "WAITING_FOR_MARKET"
                    self.stop_event.wait(0.05)
                    continue
                monotonic_now = time.monotonic()
                now_ms = self.prediction.server_timestamp_ms()
                remaining_ms = int(market["endDate"]) - now_ms
                if remaining_ms <= 0 or not self._is_current_market(market):
                    self.status = "WAITING_FOR_ROLLOVER"
                    self.stop_event.wait(0.02)
                    continue
                if (
                    monotonic_now >= self.next_settlement_recheck
                    and remaining_ms
                    > max(12_000, int(self.prediction.timeout * 1000) + 1_000)
                ):
                    self.next_settlement_recheck = (
                        monotonic_now + SETTLEMENT_RECHECK_SECONDS
                    )
                    current_market_id = int(
                        market["_selectedMarket"]["market"]["marketId"]
                    )
                    for _ in range(5):
                        if not self.reconcile_untracked_open_settlement(
                            current_market_id
                        ):
                            break
                    self.reconcile_pending_settlement()
                    now_ms = self.prediction.server_timestamp_ms()
                    remaining_ms = int(market["endDate"]) - now_ms
                    if remaining_ms <= 0 or not self._is_current_market(market):
                        continue
                if now_ms < int(market["startDate"]):
                    self.status = "WAITING_FOR_START"
                    self.stop_event.wait(
                        min(0.25, max(0.01, (int(market["startDate"]) - now_ms) / 1000))
                    )
                    continue
                start_price = market.get("variantData", {}).get("startPrice")
                if not start_price:
                    topic_id = int(market["marketTopicId"])
                    detail = self.prediction.market_detail(topic_id)
                    detail["_selectedMarket"] = select_binary_market(detail)
                    with self.market_lock:
                        now_ms = self.prediction.server_timestamp_ms()
                        if (
                            not self._is_current_market(market)
                            or int(detail["endDate"]) <= now_ms
                        ):
                            continue
                        self.market = detail
                        market = detail
                    start_price = market.get("variantData", {}).get("startPrice")
                    if not start_price:
                        self.status = "WAITING_FOR_START_PRICE"
                        self.stop_event.wait(0.20)
                        continue
                remaining_ms = int(market["endDate"]) - now_ms
                if 0 < remaining_ms <= 400:
                    # The independent supervisor owns discovery.  Avoid one
                    # gratuitous old REST call in the final scheduler ticks.
                    self.status = "WAITING_FOR_ROLLOVER"
                    self.stop_event.wait((remaining_ms + 5) / 1000)
                    continue
                selected = market["_selectedMarket"]
                market_id = int(selected["market"]["marketId"])
                with ThreadPoolExecutor(max_workers=3) as pool:
                    spot_future = pool.submit(self.spot.price, "BTCUSDT")
                    up_future = pool.submit(self.prediction.orderbook, market_id, str(selected["up"]["tokenId"]))
                    down_future = pool.submit(self.prediction.orderbook, market_id, str(selected["down"]["tokenId"]))
                    spot_price = spot_future.result()
                    spot_received_monotonic_ns = time.monotonic_ns()
                    up_book = up_future.result()
                    down_book = down_future.result()
                received_wall_ns = time.time_ns()
                received_monotonic_ns = time.monotonic_ns()
                with self.market_lock:
                    now_ms = self.prediction.server_timestamp_ms()
                    if (
                        int(market["endDate"]) <= now_ms
                        or not self._is_current_market(market)
                    ):
                        # A request begun for the old round returned after the
                        # supervisor had already rolled.  Never publish or
                        # trade that stale book under either market.
                        continue
                    self.last_spot = spot_price
                    book = binance_top_of_book(
                        up_book, down_book, current_timestamp_ms=now_ms
                    )
                    snapshot = {
                        "timestamp": utc_iso(), "topic_id": int(market["marketTopicId"]),
                        "market_id": market_id, "title": market["title"],
                        "start_price": float(start_price), "spot_price": spot_price,
                        "spot_age_ms": max(
                            0.0,
                            (received_monotonic_ns - spot_received_monotonic_ns)
                            / 1_000_000,
                        ),
                        # M5 consumes the existing USD-M aggTrade WebSocket hot
                        # path.  The 1 s REST collector deliberately leaves these
                        # nullable so a public Futures request can never take A-L
                        # or the dashboard down.
                        "futures_price": None,
                        "futures_timestamp_ms": None,
                        "futures_age_ms": None,
                        "futures_agg_trade_id": None,
                        "seconds_left": max(
                            0.0, (int(market["endDate"]) - now_ms) / 1000
                        ),
                        "up_ask": book.up_ask, "up_bid": book.up_bid,
                        "down_ask": book.down_ask, "down_bid": book.down_bid,
                        "up_ask_size": book.up_ask_size, "up_bid_size": book.up_bid_size,
                        "down_ask_size": book.down_ask_size, "down_bid_size": book.down_bid_size,
                        "book_skew_ms": book.book_skew_ms,
                        "up_book_timestamp_ms": book.up_book_timestamp_ms,
                        "down_book_timestamp_ms": book.down_book_timestamp_ms,
                        "book_age_ms": book.book_age_ms,
                        "received_wall_ns": received_wall_ns,
                        "received_monotonic_ns": received_monotonic_ns,
                        "prediction_book_received_wall_ns": received_wall_ns,
                        "prediction_book_received_monotonic_ns": received_monotonic_ns,
                        "prediction_book_age_ms": book.book_age_ms,
                        "trigger_source": "rest_poll",
                    }
                    if MARKET_OBSERVER is not None:
                        MARKET_OBSERVER.reset_market(
                            market_id,
                            float(start_price),
                            market_start_ts=int(market["startDate"]) / 1000.0,
                        )
                        # Touch evidence uses each outcome token's direct best
                        # Ask.  No bid, midpoint, last trade, or complement
                        # inference is accepted by the observer.
                        MARKET_OBSERVER.update_tick(
                            now_ts=received_wall_ns / 1_000_000_000,
                            spot_price=None,
                            up_ask=book.up_ask,
                            down_ask=book.down_ask,
                            market_id=market_id,
                            book_age_seconds=(
                                float(book.book_age_ms) / 1000.0
                                if book.book_age_ms is not None
                                else None
                            ),
                            book_skew_ms=book.book_skew_ms,
                            require_verified_book_freshness=True,
                        )
                    self.latest_snapshot = dict(snapshot)
                    if self.realtime_prediction_sink is not None:
                        self.realtime_prediction_sink(
                            direct_rest_prediction_event(
                                market_id=market_id,
                                up_book=up_book,
                                down_book=down_book,
                                received_wall_ns=received_wall_ns,
                                received_monotonic_ns=received_monotonic_ns,
                                current_timestamp_ms=now_ms,
                            )
                        )
                    fee_bps = int(market.get("feeRateBps") or 0)
                    self.store.observe(snapshot)
                    pair_snapshot = {
                        **snapshot,
                        "_up_asks": up_book.get("asks") or [],
                        "_down_asks": down_book.get("asks") or [],
                    }
                    pair_live_candidates = self.store.process_pair_arb_snapshot(
                        pair_snapshot,
                        fee_bps,
                        realtime_context={
                            "trigger_source": "dual_token_rest",
                            "pair_book_source": "independent_outcome_books",
                            "execution_eligible": True,
                            "market_data_integrity_ok": True,
                            "trigger_event_sequence": (
                                f"dual-rest:{market_id}:"
                                f"{book.up_book_timestamp_ms}:"
                                f"{book.down_book_timestamp_ms}"
                            ),
                        },
                    )
                    if self.live_signal_sink is not None:
                        for candidate in pair_live_candidates:
                            self.live_signal_sink(candidate)
                    self.store.process_intraday_targets(
                        market_id, book.up_bid, book.down_bid, fee_bps
                    )
                    self.store.process_strategy_l_targets(snapshot, fee_bps)
                    self.store.process_time_arbitrage(snapshot, fee_bps)
                    # M-family experiments are fed exclusively by the WebSocket
                    # realtime engine.  Keep the REST path for A-L and for direct
                    # unit-test fallback calls, but never race a 1 s M order here.
                    self.store.maybe_enter(
                        snapshot, fee_bps, include_m_series=False
                    )
                    self.store.process_confirmation_add_shadows(snapshot)
                    if self.confirmation_snapshot_sink is not None:
                        self.confirmation_snapshot_sink(snapshot)
                    self.store.process_confidence_stop_losses(snapshot, fee_bps)
                    self.status, self.error, self.updated_at = (
                        "LIVE", None, snapshot["timestamp"]
                    )
                with self.pending_settlement_lock:
                    pending_rollover = (
                        self.pending_rollover_settlements[0]
                        if self.pending_rollover_settlements
                        else None
                    )
                if pending_rollover is not None:
                    self.settle_previous(*pending_rollover)
                    with self.pending_settlement_lock:
                        if (
                            self.pending_rollover_settlements
                            and self.pending_rollover_settlements[0]
                            is pending_rollover
                        ):
                            self.pending_rollover_settlements.popleft()
            except Exception as exc:  # Keep the local monitor alive and expose a redacted error.
                self.status = "RATE_LIMITED" if "HTTP 429" in str(exc) else "ERROR"
                self.error = str(exc)[:300]
                if self.status == "RATE_LIMITED":
                    next_delay = max(10.0, self.prediction.retry_after_seconds or 0.0)
            elapsed = time.monotonic() - cycle_started
            self.stop_event.wait(max(0.0, next_delay - elapsed))


STORE = Store(DB_PATH)
DASHBOARD_STORE = Store.open_read_only(DB_PATH)
COLLECTOR = Collector(STORE, os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET"))
MICROSTRUCTURE: MicrostructureObserver | None = None
M_REALTIME: MSeriesRealtimeEngine | None = None
LIVE_M0W: LiveM0WEngine | None = None
MARKET_OBSERVER: MarketStateObserver | None = None
PREDICTION_HEALTH_MAX_BOOK_AGE_MS = 10_000.0


def current_prediction_market_id() -> int | None:
    market = COLLECTOR.market
    try:
        return int(market["_selectedMarket"]["market"]["marketId"]) if market else None
    except (KeyError, TypeError, ValueError):
        return None


def current_prediction_reference() -> dict[str, Any] | None:
    return dict(COLLECTOR.latest_snapshot) if COLLECTOR.latest_snapshot else None


def current_m_market_reference() -> dict[str, Any] | None:
    """Return immutable-enough market metadata for the WS M experiment clock."""
    market = COLLECTOR.market
    if not market:
        return None
    try:
        selected = market["_selectedMarket"]
        start_price = market.get("variantData", {}).get("startPrice")
        if start_price is None:
            return None
        return {
            "market_id": int(selected["market"]["marketId"]),
            "topic_id": int(market["marketTopicId"]),
            "title": str(market.get("title") or "BTC Up or Down 5m"),
            "start_price": float(start_price),
            "start_ms": int(market["startDate"]),
            "end_ms": int(market["endDate"]),
            "fee_bps": int(market.get("feeRateBps") or 0),
            "up_token_id": str(selected["up"]["tokenId"]),
            "down_token_id": str(selected["down"]["tokenId"]),
            "server_clock_offset_ms": float(
                (COLLECTOR.prediction._time_offset_ms if COLLECTOR.prediction else 0)
                or 0
            ),
        }
    except (KeyError, TypeError, ValueError):
        return None


def build_health_payload(
    *,
    collector_status: str,
    micro: dict[str, Any] | None,
    m_realtime: dict[str, Any] | None,
) -> dict[str, Any]:
    """Separate Prediction transport health from execution usability."""
    writer_status = (micro or {}).get("storage", {}).get("writerStatus")
    stream_states = (micro or {}).get("streams", {})
    legacy_spot = stream_states.get("spot") or {}
    required_streams = {
        "spot_trade": (
            stream_states.get("spot_trade") or legacy_spot
        ).get("status"),
        "spot_book": (
            stream_states.get("spot_book") or legacy_spot
        ).get("status"),
        "futures": (stream_states.get("futures") or {}).get("status"),
        "prediction": (stream_states.get("prediction") or {}).get("status"),
    }
    prediction = stream_states.get("prediction") or {}
    mapping = prediction.get("bookMapping")
    prediction_market_id = prediction.get("marketId")
    m_realtime_market_id = (m_realtime or {}).get("marketId")
    prediction_book_age_ms = (m_realtime or {}).get("predictionBookAgeMs")
    prediction_data_source = (m_realtime or {}).get("predictionDataSource")
    effective_mapping = (
        (m_realtime or {}).get("predictionOrientation")
        if prediction_data_source == "dual_token_rest"
        else mapping
    )
    try:
        ids_match = (
            prediction_market_id is not None
            and m_realtime_market_id is not None
            and int(prediction_market_id) == int(m_realtime_market_id)
        )
    except (TypeError, ValueError):
        ids_match = False
    try:
        book_age_healthy = (
            prediction_book_age_ms is not None
            and 0 <= float(prediction_book_age_ms)
            <= PREDICTION_HEALTH_MAX_BOOK_AGE_MS
        )
    except (TypeError, ValueError):
        book_age_healthy = False
    mapping_verified = effective_mapping in PREDICTION_VERIFIED_ORIENTATIONS
    direct_rest_prediction = prediction_data_source == "dual_token_rest"
    orientation_healthy = bool(
        (direct_rest_prediction or required_streams["prediction"] == "LIVE")
        and mapping_verified
        and ids_match
        and book_age_healthy
    )
    orientation_timed_out = prediction.get("orientationTimedOut") is True
    if orientation_healthy:
        orientation_status = "HEALTHY"
        orientation_reason = None
    elif required_streams["prediction"] != "LIVE" and not direct_rest_prediction:
        orientation_status = "DEGRADED"
        orientation_reason = "PREDICTION_STREAM_NOT_LIVE"
    elif orientation_timed_out:
        orientation_status = "DEGRADED"
        orientation_reason = (
            prediction.get("orientationFailureReason") or "ORIENTATION_TIMEOUT"
        )
    elif mapping_verified and not ids_match:
        orientation_status = "DEGRADED"
        orientation_reason = (
            "PREDICTION_MARKET_ID_MISMATCH"
            if prediction_market_id is not None and m_realtime_market_id is not None
            else "PREDICTION_MARKET_ID_UNAVAILABLE"
        )
    elif mapping_verified and not book_age_healthy:
        orientation_status = "DEGRADED"
        orientation_reason = (
            "PREDICTION_BOOK_AGE_UNAVAILABLE"
            if prediction_book_age_ms is None
            else "PREDICTION_BOOK_STALE"
        )
    else:
        orientation_status = "PENDING"
        orientation_reason = (
            prediction.get("orientationFailureReason")
            or "PREDICTION_ORIENTATION_NOT_VERIFIED"
        )

    healthy = bool(
        collector_status == "LIVE"
        and writer_status == "RUNNING"
        and (micro or {}).get("status") in {"LIVE", "DEGRADED", "PARTIAL"}
        and all(
            required_streams[name] == "LIVE"
            for name in ("spot_trade", "spot_book", "futures")
        )
        and (
            direct_rest_prediction
            or required_streams["prediction"] == "LIVE"
        )
        and (m_realtime or {}).get("status") == "LIVE"
        and (m_realtime or {}).get("marketDataIntegrityOk") is True
        and int((m_realtime or {}).get("droppedEvents") or 0) == 0
        and not (m_realtime or {}).get("error")
        and orientation_healthy
    )
    return {
        "ok": healthy,
        "collector": collector_status,
        "microstructure": (micro or {}).get("status"),
        "streams": required_streams,
        "microstructureWriter": writer_status,
        "mRealtime": (m_realtime or {}).get("status"),
        "mRealtimeIntegrity": (m_realtime or {}).get("marketDataIntegrityOk"),
        "mRealtimeDroppedEvents": (m_realtime or {}).get("droppedEvents"),
        "mRealtimeError": (m_realtime or {}).get("error"),
        "predictionBookMapping": mapping,
        "effectivePredictionBookMapping": effective_mapping,
        "predictionDataSource": prediction_data_source,
        "predictionMarketId": prediction_market_id,
        "mRealtimeMarketId": m_realtime_market_id,
        "predictionBookAgeMs": prediction_book_age_ms,
        "predictionBookVersionAgeMs": prediction.get("bookVersionAgeMs"),
        "predictionLocalReceiptAgeMs": prediction.get("localReceiptAgeMs"),
        "spotTradeIngressAgeMs": (m_realtime or {}).get("spotTradeIngressAgeMs"),
        "spotTradeProcessedAgeMs": (m_realtime or {}).get("spotTradeProcessedAgeMs"),
        "predictionOrientationHealthy": orientation_healthy,
        "predictionOrientationStatus": orientation_status,
        "predictionOrientationReason": orientation_reason,
    }


def realtime_dashboard_state() -> dict[str, Any]:
    """Build the dashboard hot path without touching the simulation database."""
    latest = current_prediction_reference()
    market_reference = current_m_market_reference()
    if (
        latest is not None
        and market_reference is not None
        and int(latest.get("market_id") or 0)
        == int(market_reference.get("market_id") or 0)
    ):
        server_now_ms = (
            time.time() * 1000.0
            + float(market_reference.get("server_clock_offset_ms") or 0.0)
        )
        latest["seconds_left"] = max(
            0.0,
            (float(market_reference["end_ms"]) - server_now_ms) / 1000.0,
        )

    microstructure = MICROSTRUCTURE.state() if MICROSTRUCTURE else None
    m_realtime = M_REALTIME.state() if M_REALTIME else None
    min_observer_samples = int(
        (m_realtime or {}).get("m01oMinObserverSamples") or 6
    )
    min_current_range_score = int(
        (m_realtime or {}).get("m01oMinCurrentRangeScore") or 2
    )
    market_observer = (
        MARKET_OBSERVER.state(
            m01o_min_settled_samples=min_observer_samples,
            m01o_min_current_range_score=min_current_range_score,
        )
        if MARKET_OBSERVER
        else None
    )
    return {
        "generatedAt": utc_iso(),
        "connection": {
            "status": COLLECTOR.status,
            "error": COLLECTOR.error,
            "updatedAt": COLLECTOR.updated_at,
            "intervalSeconds": COLLECTOR.interval,
            "rateLimits": (
                COLLECTOR.prediction.last_rate_limits
                if COLLECTOR.prediction
                else {}
            ),
        },
        "latest": latest,
        "microstructure": microstructure,
        "mRealtime": m_realtime,
        "marketObserver": market_observer,
    }


class Handler(BaseHTTPRequestHandler):
    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "")
        if allowed_dashboard_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-BTC-Lab-Manual-Exit",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_OPTIONS(self) -> None:
        self._headers(204)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        request_path = request.path
        if request_path == "/api/realtime":
            self._headers()
            self.wfile.write(
                json.dumps(realtime_dashboard_state()).encode("utf-8")
            )
        elif request_path == "/api/state":
            self._headers()
            state = DASHBOARD_STORE.dashboard(
                COLLECTOR,
                include_experiments=False,
            )
            # These live observers already travel through /api/realtime. Avoid
            # taking their engine locks again during the heavier statistics read.
            state["liveM0W"] = (
                LIVE_M0W.state(include_ledger=False)
                if LIVE_M0W
                else None
            )
            self.wfile.write(json.dumps(state).encode("utf-8"))
        elif request_path == "/api/trades/futures-lead-observer":
            query = parse_qs(request.query)
            try:
                page = int(query.get("page", ["1"])[0])
            except (TypeError, ValueError):
                page = 1
            self._headers()
            self.wfile.write(
                json.dumps(
                    DASHBOARD_STORE.futures_lead_observer_trade_page(
                        page=page,
                        page_size=10,
                    )
                ).encode("utf-8")
            )
        elif request_path == "/api/live-details":
            self._headers()
            self.wfile.write(
                json.dumps(LIVE_M0W.state() if LIVE_M0W else None).encode("utf-8")
            )
        elif request_path == "/api/live-rules":
            self._headers()
            self.wfile.write(
                json.dumps(
                    LIVE_M0W.state(include_ledger=False) if LIVE_M0W else None
                ).encode("utf-8")
            )
        elif request_path == "/api/experiment/m-exit":
            self._headers()
            self.wfile.write(
                json.dumps(DASHBOARD_STORE.mx_state()).encode("utf-8")
            )
        elif request_path == "/api/experiment/m0-exit":
            self._headers()
            self.wfile.write(
                json.dumps(
                    DASHBOARD_STORE.mx_state(
                        M0X_STRATEGIES,
                        signal_family="M0",
                    )
                ).encode("utf-8")
            )
        elif request_path == "/api/experiment/pair-arb":
            self._headers()
            self.wfile.write(
                json.dumps(DASHBOARD_STORE.pair_arb_state()).encode("utf-8")
            )
        elif request_path == "/health":
            self._headers()
            micro = MICROSTRUCTURE.state() if MICROSTRUCTURE else None
            m_realtime = M_REALTIME.state() if M_REALTIME else None
            self.wfile.write(
                json.dumps(
                    build_health_payload(
                        collector_status=COLLECTOR.status,
                        micro=micro,
                        m_realtime=m_realtime,
                    )
                ).encode()
            )
        else:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')

    def do_POST(self) -> None:
        if self.path not in {
            "/api/config",
            "/api/strategy-reset",
            "/api/live-control",
            "/api/live-rules",
            "/api/live-sell",
        }:
            self._headers(404)
            self.wfile.write(b'{"error":"not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            if self.path == "/api/config":
                response = {"config": STORE.update_config(payload)}
            elif self.path in {
                "/api/live-control", "/api/live-rules", "/api/live-sell"
            }:
                if LIVE_M0W is None:
                    raise ValueError("live executor is not running")
                if self.path == "/api/live-sell":
                    if not allowed_manual_sell_request(
                        str(self.client_address[0]),
                        self.headers.get("Origin", ""),
                        self.headers.get("Host", ""),
                        self.headers.get("X-BTC-Lab-Manual-Exit", ""),
                    ):
                        raise ValueError(
                            "manual sell is allowed only from this computer or "
                            "its matching private-LAN dashboard with confirmation"
                        )
                    try:
                        order_local_id = int(payload.get("orderLocalId"))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "orderLocalId must be a positive integer"
                        ) from exc
                    if order_local_id <= 0:
                        raise ValueError("orderLocalId must be a positive integer")
                    response = {
                        "liveM0W": LIVE_M0W.manual_sell(
                            order_local_id,
                            str(payload.get("orderType") or ""),
                        )
                    }
                elif self.path == "/api/live-rules":
                    if not allowed_live_rules_request(
                        str(self.client_address[0]),
                        self.headers.get("Origin", ""),
                        self.headers.get("Host", ""),
                    ):
                        raise ValueError(
                            "live rules can be changed only from this computer or its private LAN dashboard"
                        )
                    response = {
                        "liveM0W": LIVE_M0W.update_live_rules(payload)
                    }
                else:
                    action = str(payload.get("action") or "").strip().lower()
                    if action not in {"pause", "resume"}:
                        raise ValueError("action must be pause or resume")
                    if action == "resume":
                        try:
                            requester = ipaddress.ip_address(
                                str(self.client_address[0]).split("%", 1)[0]
                            )
                        except ValueError as exc:
                            raise ValueError(
                                "live resume requires a loopback request"
                            ) from exc
                        if not requester.is_loopback:
                            raise ValueError(
                                "live resume is allowed only from this computer; LAN devices may pause and monitor"
                            )
                    response = {
                        "liveM0W": LIVE_M0W.set_runtime_enabled(
                            action == "resume"
                        )
                    }
            else:
                strategy = payload.get("strategy")
                if not isinstance(strategy, str):
                    raise ValueError("strategy must be a supported strategy name")
                normalized = strategy.strip().upper()
                response = STORE.reset_strategy_measurement(normalized)
            self._headers()
            self.wfile.write(json.dumps(response).encode())
        except (ValueError, json.JSONDecodeError) as exc:
            self._headers(400)
            self.wfile.write(json.dumps({"error": str(exc)}).encode())

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    global MICROSTRUCTURE, M_REALTIME, LIVE_M0W, MARKET_OBSERVER
    restart_event = threading.Event()
    restart_reason: dict[str, str] = {"value": ""}

    def request_api_restart(reason: str) -> None:
        if restart_event.is_set():
            return
        restart_reason["value"] = str(reason)[:400]
        print(
            f"API watchdog requested restart: {restart_reason['value']}",
            flush=True,
        )
        restart_event.set()

    try:
        order_sync_restart_threshold = max(
            1,
            int(os.environ.get("PREDICT_API_RESTART_ERROR_THRESHOLD", "5")),
        )
    except ValueError:
        order_sync_restart_threshold = 5

    COLLECTOR.start()
    MARKET_OBSERVER = MarketStateObserver(db_path=str(DB_PATH))
    configured_live = str(os.environ.get("PREDICT_LIVE_ENABLED", "0")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    auto_redeem_enabled = str(
        os.environ.get("PREDICT_AUTO_REDEEM_ENABLED", "1")
    ).lower() in {"1", "true", "yes", "on"}
    live_api_key = os.environ.get("BINANCE_LIVE_API_KEY") or os.environ.get(
        "BINANCE_API_KEY"
    )
    live_api_secret = os.environ.get("BINANCE_LIVE_API_SECRET") or os.environ.get(
        "BINANCE_API_SECRET"
    )
    credential_source = (
        "BINANCE_LIVE_API_KEY"
        if os.environ.get("BINANCE_LIVE_API_KEY")
        and os.environ.get("BINANCE_LIVE_API_SECRET")
        else "BINANCE_API_KEY (shared)"
    )
    LIVE_M0W = LiveM0WEngine(
        api_key=live_api_key,
        api_secret=live_api_secret,
        configured_enabled=configured_live,
        credential_source=credential_source,
        current_market=current_m_market_reference,
        db_path=LIVE_DB_PATH,
        account_type=os.environ.get("PREDICT_LIVE_ACCOUNT_TYPE", "SPOT"),
        auto_redeem_enabled=auto_redeem_enabled,
        order_sync_restart_threshold=order_sync_restart_threshold,
        restart_request=request_api_restart,
        m0_hourly_performance=STORE.m0_hourly_guard_snapshot,
        drawdown_market_history=STORE.drawdown_control_market_history,
        current_verified_prediction_book=(
            lambda: (
                M_REALTIME.current_verified_prediction_book()
                if M_REALTIME is not None
                else None
            )
        ),
        current_spot_reference=(
            lambda: (
                M_REALTIME.current_spot_reference()
                if M_REALTIME is not None
                else None
            )
        ),
        current_direct_rest_prediction_book=(
            lambda: (
                M_REALTIME.current_direct_rest_prediction_book()
                if M_REALTIME is not None
                else None
            )
        ),
    )
    LIVE_M0W.start()
    COLLECTOR.live_signal_sink = LIVE_M0W.submit_signal
    COLLECTOR.confirmation_snapshot_sink = (
        LIVE_M0W.record_confirmation_add_snapshot
    )
    M_REALTIME = MSeriesRealtimeEngine(
        store=STORE,
        current_market=current_m_market_reference,
        live_signal_sink=LIVE_M0W.submit_signal,
        market_observer=MARKET_OBSERVER,
    )
    M_REALTIME.start()
    COLLECTOR.realtime_prediction_sink = M_REALTIME.submit
    MICROSTRUCTURE = MicrostructureObserver(
        api_key=os.environ.get("BINANCE_API_KEY"),
        api_secret=os.environ.get("BINANCE_API_SECRET"),
        current_market_id=current_prediction_market_id,
        prediction_reference=current_prediction_reference,
        realtime_event_sink=M_REALTIME.submit,
    )
    MICROSTRUCTURE.start()
    server = ThreadingHTTPServer((API_HOST, API_PORT), Handler)

    def shutdown_for_restart() -> None:
        restart_event.wait()
        server.shutdown()

    threading.Thread(
        target=shutdown_for_restart,
        name="api-restart-watchdog",
        daemon=True,
    ).start()
    print(f"Local simulation API: http://{API_HOST}:{API_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        COLLECTOR.stop_event.set()
        if MICROSTRUCTURE:
            MICROSTRUCTURE.stop()
        if M_REALTIME:
            M_REALTIME.stop()
        if LIVE_M0W:
            LIVE_M0W.stop()
        server.server_close()
    if restart_event.is_set():
        raise SystemExit(API_RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
