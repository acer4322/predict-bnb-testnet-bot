from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STRATEGIES = {
    "M01O_F1": "Observer F1 篩選後的單腿低價方向單",
    "M7_2": "開盤後 2 秒 Binance spot 動量單腿單",
    "K": "基差校正終局機率單腿單",
    "M01R": "低價觸底後反彈 0.10 的單腿確認",
    "B2": "尾盤高機率側、時間加權本金與停損",
}


@dataclass(frozen=True)
class Metrics:
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    realized_pnl: float
    roi_on_filled_cost: float | None
    profit_factor: float | None
    max_drawdown: float
    max_consecutive_losses: int


def _connect_read_only(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=60
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    return db


def _summary(rows: list[sqlite3.Row]) -> Metrics:
    pnl_values = [float(row["pnl"]) for row in rows]
    filled_cost = sum(
        float(row["stake"] or 0.0) + float(row["fees"] or 0.0)
        for row in rows
    )
    wins = sum(value > 0 for value in pnl_values)
    gross_profit = sum(max(0.0, value) for value in pnl_values)
    gross_loss = sum(max(0.0, -value) for value in pnl_values)
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    losing_streak = 0
    max_losing_streak = 0
    for value in pnl_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if value <= 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return Metrics(
        trades=len(rows),
        wins=wins,
        losses=len(rows) - wins,
        win_rate=wins / len(rows) if rows else None,
        realized_pnl=sum(pnl_values),
        roi_on_filled_cost=(sum(pnl_values) / filled_cost if filled_cost else None),
        profit_factor=(gross_profit / gross_loss if gross_loss else None),
        max_drawdown=max_drawdown,
        max_consecutive_losses=max_losing_streak,
    )


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _split_rows(
    rows: list[sqlite3.Row], start: datetime, end: datetime
) -> dict[str, list[sqlite3.Row]]:
    duration = end - start
    development_end = start + duration * 0.60
    validation_end = start + duration * 0.80
    result = {"development": [], "validation": [], "holdout": []}
    for row in rows:
        opened = _iso(str(row["opened_at"]))
        split = (
            "development"
            if opened < development_end
            else "validation" if opened < validation_end else "holdout"
        )
        result[split].append(row)
    return result


def build_report(database: Path) -> dict[str, Any]:
    db = _connect_read_only(database)
    try:
        observation = db.execute(
            """SELECT MIN(timestamp) AS first_timestamp,
                      MAX(timestamp) AS last_timestamp,
                      COUNT(*) AS rows,
                      COUNT(DISTINCT market_id) AS markets
                 FROM observations"""
        ).fetchone()
        official_markets = int(
            db.execute(
                """SELECT COUNT(DISTINCT o.market_id)
                     FROM observations AS o
                     JOIN market_settlements AS s ON s.market_id=o.market_id
                    WHERE s.status='OFFICIAL'
                      AND s.official_winner IN ('UP','DOWN')"""
            ).fetchone()[0]
        )
        spans: dict[str, dict[str, str]] = {}
        for strategy in STRATEGIES:
            row = db.execute(
                """SELECT MIN(opened_at) AS first_opened,
                          MAX(closed_at) AS last_closed
                     FROM trades
                    WHERE strategy=? AND status<>'OPEN' AND pnl IS NOT NULL""",
                (strategy,),
            ).fetchone()
            if row["first_opened"] is None or row["last_closed"] is None:
                raise RuntimeError(f"{strategy} has no realized trades")
            spans[strategy] = dict(row)

        common_start_text = max(value["first_opened"] for value in spans.values())
        common_end_text = min(value["last_closed"] for value in spans.values())
        common_start = _iso(common_start_text)
        common_end = _iso(common_end_text)
        if common_start >= common_end:
            raise RuntimeError("the selected strategies have no common realized window")

        summaries: dict[str, Any] = {}
        for strategy, description in STRATEGIES.items():
            all_rows = db.execute(
                """SELECT id, market_id, opened_at, stake, fees, pnl
                     FROM trades
                    WHERE strategy=? AND status<>'OPEN' AND pnl IS NOT NULL
                    ORDER BY opened_at, id""",
                (strategy,),
            ).fetchall()
            common_rows = [
                row
                for row in all_rows
                if common_start <= _iso(str(row["opened_at"])) <= common_end
            ]
            split_rows = _split_rows(common_rows, common_start, common_end)
            summaries[strategy] = {
                "description": description,
                "availableWindow": spans[strategy],
                "allAvailable": asdict(_summary(all_rows)),
                "commonWindow": {
                    "overall": asdict(_summary(common_rows)),
                    "splits": {
                        name: asdict(_summary(values))
                        for name, values in split_rows.items()
                    },
                },
            }

        candidates = []
        for strategy, value in summaries.items():
            overall = value["commonWindow"]["overall"]
            splits = value["commonWindow"]["splits"]
            if (
                overall["trades"] >= 100
                and overall["realized_pnl"] > 0
                and splits["validation"]["realized_pnl"] > 0
                and splits["holdout"]["realized_pnl"] > 0
            ):
                candidates.append(strategy)
        selected = min(
            candidates,
            key=lambda strategy: (
                summaries[strategy]["commonWindow"]["overall"]["max_drawdown"],
                summaries[strategy]["commonWindow"]["overall"][
                    "max_consecutive_losses"
                ],
            ),
            default=None,
        )
        return {
            "schemaVersion": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceDatabase": str(database.resolve()),
            "dataSnapshot": {
                "firstObservation": observation["first_timestamp"],
                "lastObservation": observation["last_timestamp"],
                "observationRows": int(observation["rows"]),
                "observedMarkets": int(observation["markets"]),
                "officialSettledMarketsWithObservations": official_markets,
            },
            "comparisonWindow": {
                "start": common_start_text,
                "end": common_end_text,
                "splitPolicy": "common wall-clock window 60/20/20",
            },
            "accounting": {
                "realizedOnly": True,
                "winDefinition": "trade pnl > 0",
                "roiDenominator": "sum(stake + recorded fees)",
                "feesIncluded": True,
                "openTradesExcluded": True,
                "executionModel": "recorded paper fills using observed top-of-book depth; no queue-position guarantee",
            },
            "summaries": summaries,
            "selection": {
                "rule": "common-window trades >=100, positive overall, validation and holdout PnL; then lowest drawdown and losing streak",
                "eligible": candidates,
                "selectedCandidate": selected,
                "warning": "A selected candidate is a forward-paper candidate, not proof of live positive expectancy.",
            },
        }
    finally:
        db.close()


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    snapshot = report["dataSnapshot"]
    window = report["comparisonWindow"]
    lines = [
        "# 最新五類單腿策略比較",
        "",
        f"產生時間（UTC）：`{report['generatedAt']}`",
        f"資料最後觀察（UTC）：`{snapshot['lastObservation']}`",
        f"資料量：`{snapshot['observationRows']:,}` 筆觀察、"
        f"`{snapshot['officialSettledMarketsWithObservations']:,}` 個正式結算市場。",
        f"公平比較共同區間（UTC）：`{window['start']}` ～ `{window['end']}`。",
        "",
        "所有數字只含已實現 paper 交易；勝率定義為 PnL > 0，收益已含記錄費用。",
        "",
        "| 策略 | 類型 | 交易 | 勝率 | 收益 USDT | ROI | 最大回撤 | 最大連敗 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, values in report["summaries"].items():
        metric = values["commonWindow"]["overall"]
        lines.append(
            f"| {strategy} | {values['description']} | {metric['trades']} | "
            f"{_pct(metric['win_rate'])} | {metric['realized_pnl']:.2f} | "
            f"{_pct(metric['roi_on_filled_cost'])} | {metric['max_drawdown']:.2f} | "
            f"{metric['max_consecutive_losses']} |"
        )
    lines.extend(["", "## 時序穩健性", ""])
    for strategy, values in report["summaries"].items():
        splits = values["commonWindow"]["splits"]
        lines.append(
            f"- `{strategy}`：development `{splits['development']['realized_pnl']:.2f}`，"
            f"validation `{splits['validation']['realized_pnl']:.2f}`，"
            f"holdout `{splits['holdout']['realized_pnl']:.2f}` USDT。"
        )
    selected = report["selection"]["selectedCandidate"]
    lines.extend(
        [
            "",
            "## 結論",
            "",
            (
                f"`{selected}` 是本輪唯一通過共同區間整體、validation 與 holdout "
                "皆為正的低回撤候選；但 development 仍可能為負，只能進入凍結參數的 forward paper 驗證。"
                if selected
                else "沒有策略通過本輪穩健性門檻，不應升級到 live。"
            ),
            "",
            "紙上成交仍未保證實盤排隊順位、延遲期間撤單與多檔滑價；本報告不改動任何 live 設定。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/simulation.db"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    args = parser.parse_args()
    report = build_report(args.database)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = args.output_dir / f"five-strategy-{stamp}.json"
    markdown_path = args.output_dir / f"five-strategy-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["selection"], ensure_ascii=False, indent=2))
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
