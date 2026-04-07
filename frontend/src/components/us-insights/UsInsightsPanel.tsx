"use client";

import { InlineLoading, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, Tile } from "@carbon/react";
import type { USStockInsights } from "@shared/api";

type Props = {
  isLoading: boolean;
  insights: USStockInsights | null;
};

function formatSignedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function UsInsightsPanel({ isLoading, insights }: Props) {
  if (isLoading) {
    return (
      <Tile className="app-section-tile" data-testid="us-insights-section">
        <InlineLoading description="Loading U.S. peer comparison" status="active" />
      </Tile>
    );
  }
  if (!insights) {
    return null;
  }
  const { peer } = insights;
  return (
    <Tile className="app-section-tile app-tw-insights" data-testid="us-insights-section">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">U.S. insights</p>
          <h2>Peer comparison</h2>
        </div>
        <p className="app-section-meta">
          {peer.sector} / {peer.industry} - as of {peer.asOf}
        </p>
      </div>
      <section className="app-tw-block">
        <p className="app-section-meta">
          Company rank (1M): {peer.companyRankBy1m ?? "-"} / {peer.companyCount} | Source:{" "}
          <a href={peer.compareUrl} rel="noreferrer" target="_blank">
            Yahoo Finance
          </a>
        </p>
        {peer.companyReturns ? (
          <p className="app-section-meta">
            {peer.companySymbol} return: {formatSignedPercent(peer.companyReturns.change1mPercent)} (1M),{" "}
            {formatSignedPercent(peer.companyReturns.change3mPercent)} (3M),{" "}
            {formatSignedPercent(peer.companyReturns.change6mPercent)} (6M),{" "}
            {formatSignedPercent(peer.companyReturns.change1yPercent)} (1Y)
          </p>
        ) : null}
        <Table size="sm" useZebraStyles>
          <TableHead>
            <TableRow>
              <TableHeader>#</TableHeader>
              <TableHeader>Symbol</TableHeader>
              <TableHeader>1M</TableHeader>
              <TableHeader>3M</TableHeader>
              <TableHeader>6M</TableHeader>
              <TableHeader>1Y</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {peer.topMovers.map((row) => (
              <TableRow key={`${row.rank}-${row.symbol}`}>
                <TableCell>{row.rank}</TableCell>
                <TableCell>{row.symbol}</TableCell>
                <TableCell>{formatSignedPercent(row.change1mPercent)}</TableCell>
                <TableCell>{formatSignedPercent(row.change3mPercent)}</TableCell>
                <TableCell>{formatSignedPercent(row.change6mPercent)}</TableCell>
                <TableCell>{formatSignedPercent(row.change1yPercent)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </Tile>
  );
}
