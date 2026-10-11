"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { LanguageProvider } from "./i18n";
import { AppChrome } from "../components/layout/AppChrome";

type Props = {
  children: ReactNode;
};

export function Providers({ children }: Props) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Bound retries so a down backend ends in an error state, not an endless spinner (#285).
            retry: 2,
            retryDelay: (attempt) => Math.min(8_000, 1_000 * 2 ** attempt),
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <AppChrome>{children}</AppChrome>
      </LanguageProvider>
    </QueryClientProvider>
  );
}
