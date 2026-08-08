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

test("dashboard route proxies only the read-only sidecar state", () => {
  const route = read("api/oracle-cross-market/route.ts");
  assert.match(route, /127\.0\.0\.1:8767/);
  assert.match(route, /\/state/);
  assert.doesNotMatch(route, /POST|placeOrder|live-control/);
});
