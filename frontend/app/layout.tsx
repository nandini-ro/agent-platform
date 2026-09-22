import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "./globals.css";

// Self-hosted by next/font: no request to Google at runtime, and the fallback
// metrics are matched so swapping the webfont in causes no layout shift.
const sans = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// Tool arguments, results and Garak probes are read character by character, so
// the mono face is one with a disambiguated 0/O and l/1.
const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "Agent Platform",
  description:
    "Configurable multi-agent chatbot platform with MCP tools and Garak security testing.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="bg-slate-50 text-slate-900 antialiased">{children}</body>
    </html>
  );
}
