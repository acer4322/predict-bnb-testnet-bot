import type { Metadata } from "next";
import DecisionStrategyTestPanel from "../decision-strategy-test-panel";

export const metadata: Metadata = {
  title: "決策策略測試 · BTC 5M Lab",
  description: "Rank 1 與 Rank 2 原生事件驅動決策策略的獨立 Paper／實單白名單觀測頁。",
};

export default function DecisionStrategyTestPage() {
  return <DecisionStrategyTestPanel />;
}
