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

test("market chart samples Binance and Polymarket on the same one-second buckets", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /SynchronizedTrajectoryOverlay/);
  assert.match(panel, /SYNC_SAMPLE_INTERVAL_MS = 1000/);
  assert.match(panel, /sampleBucketMs/);
  assert.match(panel, /Promise\.all/);
  assert.match(panel, /8766\/api\/realtime/);
  assert.match(panel, /bucketMs/);
  assert.match(panel, /binanceUpAsk/);
  assert.match(panel, /binanceDownAsk/);
  assert.match(panel, /polyUpAsk/);
  assert.match(panel, /polyDownAsk/);
  assert.match(panel, /last\?\.bucketMs === bucketMs/);
});

test("synchronized overlay replaces native chart drawing but preserves its visual vocabulary", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /canvas\.market-chart/);
  assert.match(panel, /base\.style\.visibility = "hidden"/);
  assert.match(panel, /TRAJECTORY_HISTORY_LIMIT = 90/);
  assert.match(panel, /slice\(-TRAJECTORY_HISTORY_LIMIT\)/);
  assert.match(panel, /ctx\.lineWidth = 2\.4/);
  assert.match(panel, /ctx\.lineJoin = "round"/);
  assert.match(panel, /BINANCE_UP_COLOR/);
  assert.match(panel, /BINANCE_DOWN_COLOR/);
  assert.match(panel, /POLY_UP_COLOR/);
  assert.match(panel, /POLY_DOWN_COLOR/);
  assert.match(panel, /dashed \? \[7, 5\] : \[\]/);
  assert.match(panel, /visiblePoints\.map\(point => point\.binanceUpAsk\)/);
  assert.match(panel, /visiblePoints\.map\(point => point\.polyUpAsk\)/);
});

test("synchronized trajectory cache survives refresh and keeps only the rolling window", () => {
  const panel = read("oracle-cross-market-panel.tsx");
  assert.match(panel, /btc5m-sync-trajectory:/);
  assert.match(panel, /window\.localStorage\.getItem/);
  assert.match(panel, /window\.localStorage\.setItem/);
  assert.match(panel, /TRAJECTORY_HISTORY_LIMIT = 90/);
});

test("dashboard route proxies only the read-only sidecar state", () => {
  const route = read("api/oracle-cross-market/route.ts");
  assert.match(route, /127\.0\.0\.1:8767/);
  assert.match(route, /\/state/);
  assert.doesNotMatch(route, /POST|placeOrder|live-control/);
});
