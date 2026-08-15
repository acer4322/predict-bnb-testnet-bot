import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const page = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const layout = fs.readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const panel = fs.readFileSync(new URL("../app/microprice-strategy-panel.tsx", import.meta.url), "utf8");
const loss = fs.readFileSync(new URL("../app/loss-streak-guard-dashboard.tsx", import.meta.url), "utf8");
const root = new URL("../app/", import.meta.url);

const ids = [
  "R_MICROPRICE_CONFIRM",
  "R_MICROPRICE_REVERSION",
  "R_MICROPRICE_NO_020_025",
  "R_MICROPRICE_UP_ONLY",
  "R_MICROPRICE_DOWN_ONLY",
  "R_MICROPRICE_LOW_010_020_ASK_LE_60",
  "R_MICROPRICE_LOW_010_020_ASK_GT_60",
  "R_MICROPRICE_CONFIRM_PRICE_SIDE_GUARD",
  "R_MICROPRICE_CONFIRM_EXIT_098",
  "R_MICROPRICE_CONFIRM_STALE_EXHAUSTED_GUARD",
  "R_MICROPRICE_CONFIRM_LOSS_STREAK_GUARD",
];

const banned = ["createPortal", "document.querySelector", "document.createElement", "MutationObserver", "setInterval", "/api/state"];

test("Microprice has one native page tab and eleven declared strategy IDs", () => {
  assert.match(page, /type StrategyView = [^;]*"microprice-strategies"/s);
  assert.match(page, /id="microprice-strategies-tab"/);
  assert.match(page, /<MicropriceStrategyPanel payload=\{state\}/);
  assert.match(page, /strategyView === "microprice-strategies"/);
  for (const id of ids) assert.equal(panel.includes(id), true, id);
  assert.equal((panel.match(/^  "R_MICROPRICE/gm) ?? []).length, 11);
});

test("Microprice cards do not use global DOM injection or their own polling", () => {
  for (const token of banned) assert.equal(panel.includes(token), false, token);
  assert.equal(panel.includes("research-strategy-grid"), false);
  assert.match(panel, /microprice-strategy-grid/);
});

test("old global card injectors are unmounted and removed", () => {
  assert.equal(layout.includes("ResearchDashboardEnhancements"), false);
  assert.equal(layout.includes("MicropriceStaleExhaustedGuardDashboard"), false);
  assert.equal(fs.existsSync(new URL("research-dashboard-enhancements.tsx", root)), false);
  assert.equal(fs.existsSync(new URL("microprice-stale-exhausted-guard-dashboard.tsx", root)), false);
});

test("loss streak live controls remain but no longer inject a research card", () => {
  assert.equal(layout.includes("LossStreakGuardDashboard"), true);
  assert.equal(loss.includes("RESEARCH_TARGET"), false);
  assert.equal(loss.includes("researchTarget"), false);
  assert.equal(loss.includes("createPortal(testCard"), false);
});
