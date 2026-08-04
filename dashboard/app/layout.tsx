import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import ConfirmationAddModeOptionGuard from "./confirmation-add-mode-option-guard";
import MicropriceSignalLifecycleDashboard from "./microprice-signal-lifecycle-dashboard";
import MicropriceVariantDashboard from "./microprice-variant-dashboard";
import MicropriceVariantV2Label from "./microprice-variant-v2-label";
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
        gap: 8,
        padding: 6,
        border: "1px solid rgba(126, 145, 178, .28)",
        borderRadius: 999,
        background: "rgba(8, 11, 18, .88)",
        backdropFilter: "blur(12px)",
      }}>
        <Link href="/" style={{ color: "#dce6ff", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>主監控</Link>
        <Link href="/xpair-canary" style={{ color: "#ffbd87", textDecoration: "none", padding: "7px 11px", borderRadius: 999 }}>XPAIR Canary</Link>
      </nav>
      {children}
      <MicropriceVariantDashboard />
      <MicropriceSignalLifecycleDashboard />
      <MicropriceVariantV2Label />
      <ConfirmationAddModeOptionGuard />
    </body>
  </html>;
}
