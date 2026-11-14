import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** Read-only build identity for deploy verification (#357). */
export async function GET() {
  return NextResponse.json({
    sha: process.env.NEXT_PUBLIC_BUILD_SHA || "unknown",
    buildTime: process.env.NEXT_PUBLIC_BUILD_TIME || "unknown",
  });
}
