"use client";

import WalletShadowLab from "../wallet-shadow-lab";

export default function WalletShadowPage() {
  return (
    <main style={{ maxWidth: 1500, margin: "0 auto", padding: "86px 20px 56px" }}>
      <header style={{ marginBottom: 18 }}>
        <span className="eyebrow">REVERSE-ENGINEERING WORKBENCH · BTC 5M ONLY</span>
        <h1 style={{ margin: "6px 0 8px" }}>BTC 5M Wallet Shadow Lab</h1>
        <p style={{ margin: 0, maxWidth: 980, color: "#93a5bb", lineHeight: 1.6 }}>
          同步觀察指定 Predict 錢包的真實已成交事件，並與完全隔離實單的 Shadow 模仿策略做事件級比較。
          研究目標是找出 Maker quote、被動庫存與 Taker 修正的共同事件結構，不以單純勝率判斷是否模仿成功。
        </p>
      </header>
      <WalletShadowLab />
    </main>
  );
}
