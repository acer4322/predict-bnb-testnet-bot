"use client";

import { useEffect, useMemo, useState } from "react";

const OBSERVER_OPTIONS = [
  { value: "F1", label: "原始 F1" },
  { value: "V2", label: "V2" },
  { value: "V3", label: "V3" },
  { value: "V4", label: "V4" },
  { value: "V6", label: "V6" },
  {
    value: "R_MICROPRICE_CONFIRM_OBSERVER_GUARD",
    label: "趨勢過渡防護 · Generic",
  },
  {
    value: "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_OBSERVER",
    label: "穩定共識 · F1 + Ask 0.60–0.90",
  },
] as const;

const SLOT_COUNT = 3;

type LiveRules = Record<string, unknown> & {
  strategy?: string;
  strategies: string[];
  strategyObserverEnabled: boolean[];
  strategyObserverVersions: string[];
};

type LiveRulesResponse = {
  rules?: LiveRules;
  liveM0W?: {
    rules?: LiveRules;
  };
  error?: string;
};

function apiUrl(path: string) {
  const hostname = window.location.hostname;
  const host = hostname.includes(":") ? `[${hostname}]` : hostname;
  return `${window.location.protocol}//${host}:8766${path}`;
}

function rulesFromPayload(payload: LiveRulesResponse): LiveRules | null {
  const rules = payload.rules ?? payload.liveM0W?.rules;
  if (!rules || !Array.isArray(rules.strategies)) return null;
  return normalizeRules(rules);
}

function normalizeRules(rules: LiveRules): LiveRules {
  const strategies = rules.strategies.map(value => String(value));
  const enabledSource = Array.isArray(rules.strategyObserverEnabled)
    ? rules.strategyObserverEnabled
    : [];
  const versionSource = Array.isArray(rules.strategyObserverVersions)
    ? rules.strategyObserverVersions
    : [];
  const strategyObserverEnabled = strategies.map((_, index) =>
    index === 3 ? false : Boolean(enabledSource[index]),
  );
  const strategyObserverVersions = strategies.map((_, index) =>
    String(versionSource[index] ?? "F1"),
  );
  return {
    ...rules,
    strategy: strategies[0] ?? String(rules.strategy ?? ""),
    strategies,
    strategyObserverEnabled,
    strategyObserverVersions,
    futuresLeadObserverEnabled: strategyObserverEnabled[0] ?? false,
    futuresLeadObserverVersion: strategyObserverVersions[0] ?? "F1",
  };
}

function observerSupported(strategy: string | undefined) {
  const normalized = String(strategy ?? "").trim().toUpperCase();
  return Boolean(normalized) && !normalized.startsWith("PAIR_ARB_");
}

function strategyLabel(strategy: string | undefined) {
  if (!strategy) return "未設定策略";
  if (strategy === "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_GUARD") {
    return "Microprice Confirm · 穩定共識 0.60–0.90";
  }
  if (strategy === "R_MICROPRICE_CONFIRM") {
    return "Microprice Confirm";
  }
  return strategy;
}

function sameObserverRules(left: LiveRules, right: LiveRules) {
  const count = Math.min(SLOT_COUNT, left.strategies.length, right.strategies.length);
  for (let index = 0; index < count; index += 1) {
    if (
      Boolean(left.strategyObserverEnabled[index]) !==
        Boolean(right.strategyObserverEnabled[index]) ||
      String(left.strategyObserverVersions[index] ?? "F1") !==
        String(right.strategyObserverVersions[index] ?? "F1")
    ) {
      return false;
    }
  }
  return true;
}

async function readPayload(response: Response): Promise<LiveRulesResponse> {
  try {
    return (await response.json()) as LiveRulesResponse;
  } catch {
    return {};
  }
}

export default function NativeObserverLiveControls() {
  const [visible, setVisible] = useState(false);
  const [rules, setRules] = useState<LiveRules | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState("等待讀取實單規則");

  const shownSlots = useMemo(
    () => Array.from(
      { length: Math.min(SLOT_COUNT, rules?.strategies.length ?? 0) },
      (_, index) => index,
    ),
    [rules],
  );

  const loadRules = async (message = "已讀取後端現行規則") => {
    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        cache: "no-store",
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(payload.error ?? `讀取失敗：HTTP ${response.status}`);
      }
      const next = rulesFromPayload(payload);
      if (!next) throw new Error("後端回應缺少 live rules");
      setRules(next);
      setDirty(false);
      setStatus(message);
      return next;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "讀取實單規則失敗");
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const onRootPage = window.location.pathname === "/";
    setVisible(onRootPage);
    if (onRootPage) void loadRules();
  }, []);

  const updateEnabled = (index: number, enabled: boolean) => {
    setRules(current => {
      if (!current) return current;
      const nextEnabled = [...current.strategyObserverEnabled];
      nextEnabled[index] = enabled;
      return normalizeRules({
        ...current,
        strategyObserverEnabled: nextEnabled,
      });
    });
    setDirty(true);
    setStatus("Observer 草稿尚未套用");
  };

  const updateVersion = (index: number, version: string) => {
    setRules(current => {
      if (!current) return current;
      const nextVersions = [...current.strategyObserverVersions];
      nextVersions[index] = version;
      return normalizeRules({
        ...current,
        strategyObserverVersions: nextVersions,
      });
    });
    setDirty(true);
    setStatus("Observer 草稿尚未套用");
  };

  const saveRules = async () => {
    if (!rules || saving) return;
    if (!window.confirm(
      "確定套用這三個策略槽的 Observer 設定？其他實單策略、金額與風控欄位會保持後端目前值。",
    )) return;

    setSaving(true);
    setStatus("正在儲存 Observer 規則…");
    const submitted = normalizeRules(rules);
    try {
      const response = await fetch(apiUrl("/api/live-rules"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(submitted),
      });
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(payload.error ?? `儲存失敗：HTTP ${response.status}`);
      }

      const returned = rulesFromPayload(payload);
      if (returned && !sameObserverRules(submitted, returned)) {
        throw new Error("POST 成功，但回應中的 Observer 規則與送出值不一致");
      }

      const verified = await loadRules("Observer 規則已儲存並完成重新讀取驗證");
      if (!verified || !sameObserverRules(submitted, verified)) {
        throw new Error("儲存後重新讀取不一致；實單保持原狀，請勿恢復執行");
      }
      setRules(verified);
      setDirty(false);
      setStatus("Observer 規則已儲存；重新整理後仍會保留");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Observer 規則儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  if (!visible) return null;

  return (
    <section
      aria-label="實單 Observer 原生設定"
      style={{
        margin: "76px auto 18px",
        width: "min(1180px, calc(100% - 32px))",
        padding: 18,
        border: "1px solid rgba(126, 227, 245, .32)",
        borderRadius: 16,
        background: "linear-gradient(145deg, rgba(10, 22, 29, .96), rgba(8, 11, 18, .96))",
        boxShadow: "0 18px 55px rgba(0, 0, 0, .24)",
      }}
    >
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap" }}>
        <div>
          <span style={{ color: "#7ee3f5", fontSize: 12, letterSpacing: ".12em" }}>
            NATIVE REACT · EXISTING LIVE-RULES API
          </span>
          <h2 style={{ margin: "6px 0 4px" }}>實單 Observer 原生設定</h2>
          <p style={{ margin: 0, color: "#aeb9cc", lineHeight: 1.65 }}>
            直接使用既有 strategyObserverEnabled／strategyObserverVersions 儲存欄位；不修改 DOM、不攔截 fetch、不建立 MutationObserver，也不定時輪詢。
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" disabled={loading || saving} onClick={() => void loadRules("已重新讀取後端現行規則")}>
            {loading ? "讀取中…" : "重新讀取"}
          </button>
          <button type="button" disabled={!rules || !dirty || saving} onClick={() => void saveRules()}>
            {saving ? "儲存中…" : "確認並套用 Observer"}
          </button>
        </div>
      </div>

      <p style={{ margin: "12px 0", color: dirty ? "#ffbd87" : "#8df4c0" }}>
        {status}
      </p>

      {!rules ? (
        <p style={{ color: "#ffbd87" }}>尚未取得實單規則，Observer 控制保持停用。</p>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {shownSlots.map(index => {
            const strategy = rules.strategies[index];
            const supported = observerSupported(strategy);
            return (
              <article
                key={`${strategy}-${index}`}
                style={{
                  padding: 14,
                  border: "1px solid rgba(126, 145, 178, .24)",
                  borderRadius: 12,
                  background: "rgba(255, 255, 255, .025)",
                }}
              >
                <small style={{ color: "#8f9bab" }}>策略槽 {index + 1}</small>
                <strong style={{ display: "block", margin: "5px 0 12px", overflowWrap: "anywhere" }}>
                  {strategyLabel(strategy)}
                </strong>

                <label style={{ display: "grid", gap: 6, marginBottom: 10 }}>
                  <span>是否使用 Observer</span>
                  <select
                    disabled={!supported || saving}
                    value={rules.strategyObserverEnabled[index] ? "enabled" : "disabled"}
                    onChange={event => updateEnabled(index, event.target.value === "enabled")}
                  >
                    <option value="disabled">不使用 Observer</option>
                    <option value="enabled">使用 Observer</option>
                  </select>
                </label>

                <label style={{ display: "grid", gap: 6 }}>
                  <span>Observer 版本</span>
                  <select
                    disabled={!supported || saving}
                    value={rules.strategyObserverVersions[index] ?? "F1"}
                    onChange={event => updateVersion(index, event.target.value)}
                  >
                    {OBSERVER_OPTIONS.map(option => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>

                <small style={{ display: "block", marginTop: 10, color: supported ? "#8f9bab" : "#ffbd87", lineHeight: 1.55 }}>
                  {supported
                    ? "資料缺失、版本不支援或市場不一致時，後端一律 fail closed。"
                    : "配對型策略不套用單邊 Observer；此槽保持停用。"}
                </small>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
