import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const page = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const layout = fs.readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const strong = fs.readFileSync(new URL("../app/strong-trend-guard-panel.tsx", import.meta.url), "utf8");
const calibrated = fs.readFileSync(new URL("../app/calibrated-confirmation-lab.tsx", import.meta.url), "utf8");

const banned = ["MutationObserver", "createPortal", "document.querySelector", "document.createElement", "setInterval", "/api/state"];

test("Strong Trend Guard is a native page tab with no injector or polling", () => {
  assert.match(page, /type StrategyView = [^;]*"strong-trend-guard"/s);
  assert.match(page, /id="strong-trend-guard-tab"/);
  assert.match(page, /<StrongTrendGuardPanel/);
  for (const token of banned) assert.equal(strong.includes(token), false, token);
});

test("Calibrated five-way confirmation is native and unmounted from layout", () => {
  assert.match(page, /id="calibrated-value-confirmation-tab"/);
  assert.match(page, /<CalibratedConfirmationLab payload=\{state\}/);
  assert.equal(layout.includes("CalibratedConfirmationLab"), false);
  for (const token of banned) assert.equal(calibrated.includes(token), false, token);
});

test("Reliability mirror remains the existing native React panel", () => {
  assert.match(page, /function ReliabilityShadowPanel/);
  assert.match(page, /strategyView === "reliability-shadow" \? <ReliabilityShadowPanel/);
  assert.equal(layout.includes("ReliabilityShadow"), false);
});
