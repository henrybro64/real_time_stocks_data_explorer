"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { LanguageProvider } from "./i18n";
import { AppChrome } from "../components/layout/AppChrome";

type Props = {
  children: ReactNode;
};

export function Providers({ children }: Props) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <LanguageProvider>
        <AppChrome>{children}</AppChrome>
      </LanguageProvider>
    </QueryClientProvider>
  );
}
