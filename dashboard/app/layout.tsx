import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import ConfirmationAddModeOptionGuardFast from "./confirmation-add-mode-option-guard-fast";
import LossStreakGuardDashboard from "./loss-streak-guard-dashboard";
import MarketChartRecovery from "./market-chart-recovery";
import MaximumNetLossGuardDashboard from "./maximum-net-loss-guard-dashboard";
import NativeObserverLiveControls from "./native-observer-live-controls";
import OracleCrossMarketPanel from "./oracle-cross-market-panel";
import OracleCrossStrategyPanel from "./oracle-cross-strategy-panel";
import PolyCrossTabContextBridge from "./poly-cross-tab-context-bridge";
import PolyQuoteCanaryPanel from "./poly-quote-canary-panel";
import "./globals.css";

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "BTC 5M Lab · M 系列模擬實驗 + 實單 Canary",
  description: "M 系列策略、正式實單監控，以及隔離的 BTC／ETH 非原子兩腿執行 Canary。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-TW">
    <body className={`${geist.variable} ${mono.variable}`}>
      <nav aria-label="BTC 5M Lab 頁面" style={{
        position: "fixed",
        top: 12,
        right: 12,
        zIndex: 1000,
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "flex-end",
        gap: 8,
        padding: 6,
        maxWidth: "calc(100vw - 24px)",
        border: "1px solid rgba(126, 145, 178, .28)",
        borderRadius: 999,
        background: "rgba(8, 11, 18, .88)",
        backdropFilter: "blur(12px)",
      }}>
        <Link href="/" style={{ color: "#dce6ff", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>主監控</Link>
        <Link href="/microprice-lifecycle" style={{ color: "#7ee3f5", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>Microprice 生命週期</Link>
        <Link href="/microprice-lifecycle-comparison" style={{ color: "#a5f2ba", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>Microprice A/B</Link>
        <Link href="/xpair-canary" style={{ color: "#ffbd87", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>XPAIR Canary</Link>
      </nav>
      <NativeObserverLiveControls />
      <PolyCrossTabContextBridge />
      <MarketChartRecovery />
      {children}
      <OracleCrossMarketPanel />
      <OracleCrossStrategyPanel />
      <PolyQuoteCanaryPanel />
      <LossStreakGuardDashboard />
      <MaximumNetLossGuardDashboard />
      <ConfirmationAddModeOptionGuardFast />
    </body>
  </html>;
}