import { describe, expect, it } from "vitest";

import { formatCurrency, formatSignedNumber } from "./quote";

describe("formatCurrency", () => {
  it("keeps two decimals at and above one dollar", () => {
    expect(formatCurrency(900, "USD")).toBe("$900.00");
    expect(formatCurrency(1, "USD")).toBe("$1.00");
  });

  it("keeps four decimals under one dollar", () => {
    expect(formatCurrency(0.1235, "USD")).toBe("$0.1235");
    expect(formatCurrency(0.12, "USD")).toBe("$0.12");
    expect(formatSignedNumber(0.0035, "USD")).toBe("+$0.0035");
    expect(formatSignedNumber(-0.0035, "USD")).toBe("-$0.0035");
  });
});
