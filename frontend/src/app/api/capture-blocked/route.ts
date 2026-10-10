import { NextResponse } from "next/server";

/** Capture endpoints are not exposed through the public frontend proxy (#267). */
export function POST() {
  return NextResponse.json({ detail: "Not Found" }, { status: 404 });
}

export function GET() {
  return NextResponse.json({ detail: "Not Found" }, { status: 404 });
}
