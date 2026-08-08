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

test("root layout mounts the cross-oracle strategy bridge", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /OracleCrossStrategyPanel/);
  assert.match(layout, /<OracleCrossStrategyPanel\s*\/>/);
  assert.match(layout, /PolyCrossTabContextBridge/);
  assert.match(layout, /<PolyCrossTabContextBridge\s*\/>/);
});

test("cross-oracle strategies render as an independent lower strategy tab", () => {
  const panel = read("oracle-cross-strategy-panel.tsx");
  assert.match(panel, /poly-cross-market-tab/);
  assert.match(panel, /poly-cross-market-panel/);
  assert.match(panel, /strategy-tabs/);
  assert.match(panel, /Poly 跨市場/);
  assert.match(panel, /poly-cross-market-active/);
  assert.doesNotMatch(panel, /querySelector\("\.market-panel"\)/);
});

test("Poly tab first switches the native dashboard into simulation research context", () => {
  const bridge = read("poly-cross-tab-context-bridge.tsx");
  assert.match(bridge, /button\.poly-cross-tab/);
  assert.match(bridge, /research-tab/);
  assert.match(bridge, /research\.click\(\)/);
});

test("strategy panel keeps all three lead gap experiments", () => {
  const panel = read("oracle-cross-strategy-panel.tsx");
  assert.match(panel, /R_POLY_LEAD_ENTRY/);
  assert.match(panel, /R_POLY_LEAD_EXIT/);
  assert.match(panel, /R_POLY_GAP_SCALP/);
  assert.match(panel, /Binance Ask/);
  assert.match(panel, /Binance Bid/);
  assert.match(panel, /\/api\/oracle-cross-strategies/);
});

test("strategy panel exposes five source-mirror confidence exits and protection metrics", () => {
  const panel = read("oracle-cross-strategy-panel.tsx");
  for (const strategy of [
    "R_CALIBRATED_VALUE",
    "R_MICROPRICE",
    "R_MICROPRICE_CONFIRM",
    "R_FUTURES_LEAD",
    "R_OFI",
  ]) {
    assert.match(panel, new RegExp(strategy));
  }
  assert.match(panel, /避免虧損/);
  assert.match(panel, /犧牲獲利/);
  assert.match(panel, /淨保護/);
  assert.match(panel, /若不退場 PnL/);
  assert.match(panel, /confidenceShadows/);
});

test("dashboard route only proxies the local Paper strategy sidecar", () => {
  const route = read("api/oracle-cross-strategies/route.ts");
  assert.match(route, /127\.0\.0\.1:8768/);
  assert.match(route, /\/state/);
  assert.doesNotMatch(route, /POST|placeOrder|live-control|live-sell/);
});
