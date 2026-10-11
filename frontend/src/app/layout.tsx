import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";
import "@carbon/styles/css/styles.css";
import "../styles/global.css";
import { Providers } from "./providers";

// Vendored latin files. `next/font/google` downloads CSS at build time, and
// Google sometimes returns extensionless font URLs that fail `next build`.
const bodyFont = localFont({
  src: "../fonts/exo-2-latin.woff2",
  weight: "400 700",
  style: "normal",
  display: "swap",
  variable: "--font-body",
});

const displayFont = localFont({
  src: [
    { path: "../fonts/rajdhani-latin-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/rajdhani-latin-600.woff2", weight: "600", style: "normal" },
    { path: "../fonts/rajdhani-latin-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-display",
});

export const metadata: Metadata = {
  title: "Stock Ticker",
  description: "Real-time stock dashboard",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className={`${bodyFont.variable} ${displayFont.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
