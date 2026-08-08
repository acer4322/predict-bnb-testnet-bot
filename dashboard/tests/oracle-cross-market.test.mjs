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

test("root layout mounts the cross-oracle panel", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /OracleCrossMarketPanel/);
  assert.match(layout, /<OracleCrossMarketPanel\s*\/>/);
});

test("panel renders Polymarket BTC 5m using the existing market card vocabulary", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /BTC Up or Down 5m/);
  assert.match(panel, /Chainlink BTC\/USD/);
  assert.match(panel, /UP 最佳賣價/);
  assert.match(panel, /DOWN 最佳賣價/);
  assert.match(panel, /READ ONLY/);
  assert.match(panel, /\/api\/oracle-cross-market/);
});

test("panel overlays Polymarket asks with the native 90-observation rolling window", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /PolyTrajectoryOverlay/);
  assert.match(panel, /canvas\.market-chart/);
  assert.match(panel, /poly-market-chart-overlay/);
  assert.match(panel, /Poly UP/);
  assert.match(panel, /Poly DOWN/);
  assert.match(panel, /TRAJECTORY_HISTORY_LIMIT = 90/);
  assert.match(panel, /slice\(-TRAJECTORY_HISTORY_LIMIT\)/);
  assert.match(panel, /ctx\.lineWidth = 2\.4/);
  assert.match(panel, /ctx\.lineJoin = "round"/);
  assert.match(panel, /ctx\.setLineDash\(\[7, 5\]\)/);
  assert.match(panel, /i \/ Math\.max\(1, values\.length - 1\)/);
  assert.doesNotMatch(panel, /elapsed \/ elapsedNow/);
  assert.match(panel, /visiblePoints\.map\(point => point\.upAsk\)/);
  assert.match(panel, /visiblePoints\.map\(point => point\.downAsk\)/);
});

test("Polymarket live trajectory cache survives refresh but rolls like Binance", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /btc5m-poly-trajectory:/);
  assert.match(panel, /window\.localStorage\.getItem/);
  assert.match(panel, /window\.localStorage\.setItem/);
  assert.match(panel, /TRAJECTORY_HISTORY_LIMIT = 90/);
  assert.match(panel, /secondsLeft/);
});

test("dashboard route proxies only the read-only sidecar state", () => {
  const route = read("api/oracle-cross-market/route.ts");
  assert.match(route, /127\.0\.0\.1:8767/);
  assert.match(route, /\/state/);
  assert.doesNotMatch(route, /POST|placeOrder|live-control/);
});
