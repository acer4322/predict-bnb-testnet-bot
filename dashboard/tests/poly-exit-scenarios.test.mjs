import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "../app");
const panel = fs.readFileSync(path.join(app, "poly-execution-canary-panel.tsx"), "utf8");
const comparison = fs.readFileSync(path.join(app, "poly-strategy-scenario-comparison.tsx"), "utf8");
const dedicatedLive = fs.readFileSync(path.join(app, "poly-gap-live-dashboard.tsx"), "utf8");
const layout = fs.readFileSync(path.join(app, "layout.tsx"), "utf8");

test("exit panel distinguishes observed depth from optimistic no-depth bound", () => {
  assert.match(panel, /FIRST_LEVEL_DEPTH/);
  assert.match(panel, /UPPER_BOUND_NO_DEPTH/);
  assert.match(panel, /無深度：樂觀上限/);
  assert.match(panel, /完全賣不掉/);
});

test("exit panel exposes legacy conversion and official settlement outcomes", () => {
  assert.match(panel, /sim_legacy_exit_migrated/);
  assert.match(panel, /sim_official_winner/);
  assert.match(panel, /sim_best_final_pnl_usdt/);
  assert.match(panel, /sim_no_sell_final_pnl_usdt/);
});

test("best and worst scenario rows are appended inside the three original strategy cards", () => {
  assert.match(comparison, /\.m-exit-summary-grid\.research-strategy-grid > \.m-exit-card/);
  assert.match(comparison, /createPortal\(/);
  assert.match(comparison, /host\.card/);
  assert.match(comparison, /實際進場後的執行情境對比/);
  assert.match(comparison, /label="最佳狀況"/);
  assert.match(comparison, /label="最糟狀況"/);
  assert.match(comparison, /交易／持倉/);
  assert.match(comparison, /勝率/);
  assert.match(comparison, /Gross PnL/);
});

test("dedicated Poly GAP live panel exposes stake and maximum-loss controls", () => {
  assert.match(layout, /PolyGapLiveDashboard/);
  assert.match(layout, /<PolyGapLiveDashboard\s*\/>/);
  assert.match(dedicatedLive, /R_POLY_GAP_SCALP 專用實單/);
  assert.match(dedicatedLive, /每單金額（USDT）/);
  assert.match(dedicatedLive, /最大虧損保護/);
  assert.match(dedicatedLive, /最大虧損（USDT）/);
  assert.match(dedicatedLive, /PREDICT_POLY_GAP_LIVE_ENABLED/);
  assert.match(dedicatedLive, /同一 5 分鐘市場可多輪/);
  assert.match(dedicatedLive, /MARKET\/FOK|MARKET\/FOK/);
});
