import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@carbon/styles/css/styles.css";
import "../styles/global.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Stock Ticker",
  description: "Real-time stock dashboard",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
