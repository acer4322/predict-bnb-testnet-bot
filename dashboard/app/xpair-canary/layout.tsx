import type { ReactNode } from "react";
import BookMonitorPanel from "./BookMonitorPanel";
import PaperSimulationPanel from "./PaperSimulationPanel";

export default function XPairLayout({ children }: { children: ReactNode }) {
  return <>
    <nav style={{
      position: "sticky",
      top: 0,
      zIndex: 50,
      display: "flex",
      gap: 12,
      padding: "10px 18px",
      borderBottom: "1px solid #29353b",
      background: "rgba(7, 12, 15, 0.94)",
      backdropFilter: "blur(10px)",
      fontFamily: "monospace",
      fontSize: 12,
    }}>
      <a href="/xpair-canary" style={{ color: "#b8c5ca" }}>XPAIR 監控</a>
      <a href="/xpair-canary/safety" style={{ color: "#ffb45c" }}>事故安全台</a>
      <a href="/" style={{ marginLeft: "auto", color: "#8e9ba0" }}>主監控</a>
    </nav>
    <BookMonitorPanel />
    <PaperSimulationPanel />
    {children}
  </>;
}
