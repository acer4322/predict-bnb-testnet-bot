import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "../app");
const layout = fs.readFileSync(path.join(app, "layout.tsx"), "utf8");
const trajectory = fs.readFileSync(path.join(app, "poly-live-trajectory-dashboard.tsx"), "utf8");
const operations = fs.readFileSync(path.join(app, "poly-gap-live-operations-dashboard.tsx"), "utf8");

test("live controls page mounts synchronized Poly trajectory", () => {
  assert.match(layout, /PolyLiveTrajectoryDashboard/);
  assert.match(layout, /<PolyLiveTrajectoryDashboard\s*\/>/);
  assert.match(trajectory, /\.live-rules-editor/);
  assert.match(trajectory, /\/api\/oracle-cross-market/);
  assert.match(trajectory, /8766\/api\/realtime/);
  assert.match(trajectory, /Binance UP/);
  assert.match(trajectory, /Binance DOWN/);
  assert.match(trajectory, /Poly UP/);
  assert.match(trajectory, /Poly DOWN/);
  assert.match(trajectory, /WAITING_GAMMA/);
});

test("dedicated GAP operations separates current state from last error", () => {
  assert.match(layout, /PolyGapLiveOperationsDashboard/);
  assert.match(layout, /<PolyGapLiveOperationsDashboard\s*\/>/);
  assert.match(operations, /當前狀況/);
  assert.match(operations, /上次錯誤訊息/);
  assert.match(operations, /歷史錯誤不代表現在仍處於故障/);
  assert.match(operations, /currentStatus/);
  assert.match(operations, /lastErrorDetail/);
});

test("dedicated GAP operations shows entry records messages and confirmed-entry rate", () => {
  assert.match(operations, /成功開單率/);
  assert.match(operations, /ENTRY_CONFIRMED \/ round entry attempts/);
  assert.match(operations, /開單紀錄/);
  assert.match(operations, /最近訊息/);
  assert.match(operations, /recentRounds/);
  assert.match(operations, /recentEvents/);
  assert.match(operations, /quoteRejected/);
  assert.match(operations, /edgeGoneAfterQuote/);
  assert.match(operations, /placeRejected/);
  assert.match(operations, /ambiguous/);
});
