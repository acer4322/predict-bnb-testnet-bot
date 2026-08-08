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

test("root layout mounts the cross-oracle strategy panel", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /OracleCrossStrategyPanel/);
  assert.match(layout, /<OracleCrossStrategyPanel\s*\/>/);
});

test("strategy panel exposes all three forward Paper experiments", () => {
  const panel = read("oracle-cross-strategy-panel.tsx");
  assert.match(panel, /R_POLY_LEAD_ENTRY/);
  assert.match(panel, /R_POLY_LEAD_EXIT/);
  assert.match(panel, /R_POLY_GAP_SCALP/);
  assert.match(panel, /Binance Ask/);
  assert.match(panel, /Binance Bid/);
  assert.match(panel, /gross PnL/);
  assert.match(panel, /\/api\/oracle-cross-strategies/);
});

test("dashboard route only proxies the local Paper strategy sidecar", () => {
  const route = read("api/oracle-cross-strategies/route.ts");
  assert.match(route, /127\.0\.0\.1:8768/);
  assert.match(route, /\/state/);
  assert.doesNotMatch(route, /POST|placeOrder|live-control|live-sell/);
});
