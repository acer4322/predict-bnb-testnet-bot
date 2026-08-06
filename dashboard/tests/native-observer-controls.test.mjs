import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const sourceUrl = new URL("../app/native-observer-live-controls.tsx", import.meta.url);
const layoutUrl = new URL("../app/layout.tsx", import.meta.url);

async function source() {
  return await readFile(sourceUrl, "utf8");
}

test("native Observer controls use the existing live-rules API", async () => {
  const text = await source();
  assert.match(text, /strategyObserverEnabled/);
  assert.match(text, /strategyObserverVersions/);
  assert.match(text, /\/api\/live-rules/);
  assert.match(text, /R_MICROPRICE_CONFIRM_OBSERVER_GUARD/);
  assert.match(text, /R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_OBSERVER/);
  assert.match(text, /sameObserverRules/);
});

test("native Observer controls do not use the failed sidecar techniques", async () => {
  const text = await source();
  assert.doesNotMatch(text, /MutationObserver/);
  assert.doesNotMatch(text, /document\.querySelector/);
  assert.doesNotMatch(text, /dispatchEvent/);
  assert.doesNotMatch(text, /window\.fetch\s*=/);
  assert.doesNotMatch(text, /setInterval/);
});

test("root layout mounts only the native Observer control", async () => {
  const text = await readFile(layoutUrl, "utf8");
  assert.match(text, /NativeObserverLiveControls/);
  assert.doesNotMatch(text, /GenericObserverSlotUnlock/);
  assert.doesNotMatch(text, /StableConsensusLiveControls/);
  assert.doesNotMatch(text, /MicropriceConfirmObserverGenericDashboard/);
  assert.doesNotMatch(text, /ObserverPaperAccountingStatus/);
  assert.doesNotMatch(text, /StableConsensusObserverSimulation/);
});
