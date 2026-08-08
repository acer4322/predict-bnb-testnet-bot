import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "../app");

function read(relative) {
  return fs.readFileSync(path.join(app, relative), "utf8");
}

test("root layout mounts Poly execution canary", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /PolyExecutionCanaryPanel/);
  assert.match(layout, /<PolyExecutionCanaryPanel\s*\/>/);
  assert.doesNotMatch(layout, /<PolyQuoteCanaryPanel\s*\/>/);
});

test("entry canary is explicitly signed BUY quote quality", () => {
  const panel = read("poly-execution-canary-panel.tsx");
  assert.match(panel, /ENTRY REAL QUOTE/);
  assert.match(panel, /signed BUY get-quote/);
  assert.match(panel, /進場 Signed Quote 可執行率/);
  assert.match(panel, /quote_rtt_ms/);
  assert.match(panel, /signal_to_quote_response_ms/);
  assert.match(panel, /quote_coverage_ratio/);
  assert.match(panel, /quote_expiry_headroom_ms/);
  assert.match(panel, /configuredEntryStakesUsdt/);
  assert.match(panel, /R_POLY_LEAD_ENTRY/);
  assert.match(panel, /R_POLY_LEAD_EXIT/);
  assert.match(panel, /R_POLY_GAP_SCALP/);
});

test("exit canary is scenario simulation rather than signed SELL quote", () => {
  const panel = read("poly-execution-canary-panel.tsx");
  assert.match(panel, /NO SIGNED SELL QUOTE/);
  assert.match(panel, /最佳可見 vs 完全賣不掉/);
  assert.match(panel, /sim_exit_bid_size/);
  assert.match(panel, /sim_best_fill_ratio/);
  assert.match(panel, /sim_best_final_pnl_usdt/);
  assert.match(panel, /sim_no_sell_final_pnl_usdt/);
  assert.match(panel, /官方結算/);
});
