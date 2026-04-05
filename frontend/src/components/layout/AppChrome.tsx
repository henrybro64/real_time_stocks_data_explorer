"use client";

import { type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Shell } from "./Shell";

type Props = {
  children: ReactNode;
};

export function AppChrome({ children }: Props) {
  const pathname = usePathname();
  if (pathname === "/login") {
    return <>{children}</>;
  }
  return <Shell>{children}</Shell>;
}
