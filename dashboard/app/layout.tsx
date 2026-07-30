import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geist = Geist({ variable: "--font-geist", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "BTC 5M Lab · M 系列模擬實驗 + M0W 實單",
  description: "以 22 個獨立 M 系列 ID、M01 F2／F1／LIVE 過濾對照與 1／2／3／5 秒延遲實驗為主，另設可調策略正式實單分頁與獨立實單帳本，並保留 A–L 舊策略、本地帳本與時間診斷。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-TW"><body className={`${geist.variable} ${mono.variable}`}>{children}</body></html>;
}
