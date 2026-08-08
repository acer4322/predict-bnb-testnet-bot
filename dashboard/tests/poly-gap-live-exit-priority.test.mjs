import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "..", "app", "poly-gap-live-operations-dashboard.tsx");
const source = readFileSync(app, "utf8");

test("dedicated Poly GAP live dashboard separates entry and exit execution quality", () => {
  assert.match(source, /成功開單率/);
  assert.match(source, /成功出場率/);
  assert.match(source, /exitExecution/);
  assert.match(source, /quoteRejectedEvents/);
  assert.match(source, /placeRejectedEvents/);
  assert.match(source, /notFlatRetryEvents/);
});

test("dashboard exposes asymmetric entry and exit execution tuning", () => {
  assert.match(source, /entrySlippageBps/);
  assert.match(source, /exitSlippageBps/);
  assert.match(source, /entryPositionSyncTimeoutMs/);
  assert.match(source, /exitPositionSyncTimeoutMs/);
  assert.match(source, /ENTRY executable edge/);
});
