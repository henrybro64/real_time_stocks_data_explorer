"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
  Tile,
} from "@carbon/react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { PortfolioAsset } from "@shared/api";
import { useLanguage } from "../../app/i18n";
import { usePortfolioPositions } from "../../hooks/usePortfolioPositions";
import {
  addPortfolioAsset,
  deletePortfolioAsset,
  fetchPortfolioAssets,
  fetchUsdTwdFxRate,
} from "../../services/api/client";
import { formatCurrency } from "../../services/format/quote";

export function AssetsPage() {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [assetTypeDraft, setAssetTypeDraft] = useState("cash");
  const [assetNameDraft, setAssetNameDraft] = useState("");
  const [assetCurrencyDraft, setAssetCurrencyDraft] = useState("USD");
  const [assetValueDraft, setAssetValueDraft] = useState("");
  const portfolio = usePortfolioPositions();

  const assetsQuery = useQuery<PortfolioAsset[]>({
    queryKey: ["portfolio-assets"],
    queryFn: fetchPortfolioAssets,
  });
  const fxQuery = useQuery({
    queryKey: ["usd-twd-fx-rate"],
    queryFn: fetchUsdTwdFxRate,
    refetchInterval: 60_000,
  });
  const addAssetMutation = useMutation({
    mutationFn: addPortfolioAsset,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio-assets"] });
      setAssetNameDraft("");
      setAssetValueDraft("");
    },
  });
  const deleteAssetMutation = useMutation({
    mutationFn: deletePortfolioAsset,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio-assets"] });
    },
  });

  const items = portfolio.positions;
  const assets = assetsQuery.data ?? [];
  const usdAssets = assets.filter((asset) => asset.currency === "USD");
  const twdAssets = assets.filter((asset) => asset.currency === "TWD");
  const totalUsd = [...items, ...assets].reduce((sum, entry) => {
    const currency = "stock" in entry ? entry.stock.currency : entry.currency;
    const value = "stock" in entry ? entry.marketValue : entry.value;
    return currency === "USD" ? sum + value : sum;
  }, 0);
  const totalTwd = [...items, ...assets].reduce((sum, entry) => {
    const currency = "stock" in entry ? entry.stock.currency : entry.currency;
    const value = "stock" in entry ? entry.marketValue : entry.value;
    return currency === "TWD" ? sum + value : sum;
  }, 0);
  const usdTwdRate = fxQuery.data?.rate ?? 0;
  const combinedUsd = usdTwdRate > 0 ? totalUsd + totalTwd / usdTwdRate : totalUsd;
  const combinedTwd = usdTwdRate > 0 ? totalTwd + totalUsd * usdTwdRate : totalTwd;

  const handleAddAsset = () => {
    // Be tolerant of user input like "1,000.50"
    const sanitized = String(assetValueDraft).replace(/,/g, "").trim();
    const parsedValue = Number(sanitized);
    if (!assetNameDraft.trim() || !Number.isFinite(parsedValue) || parsedValue <= 0) {
      return;
    }
    addAssetMutation.mutate({
      assetType: assetTypeDraft,
      name: assetNameDraft.trim(),
      currency: assetCurrencyDraft.trim().toUpperCase(),
      value: parsedValue,
      notes: null,
    });
  };

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-hero-tile app-portfolio-hero">
          <div className="app-hero-content">
            <h1 className="app-hero-title">{t("portfolio.assets.title")}</h1>
          </div>
          <p className="app-section-meta">{t("portfolio.assets.count", { count: assets.length })}</p>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-section-header">
            <div>
              <p className="app-eyebrow">{t("portfolio.aggregate.eyebrow")}</p>
              <h2>{t("portfolio.aggregate.title")}</h2>
            </div>
          </div>
          <div className="app-assets-currency-columns">
            <div className="app-portfolio-aggregate-card">
              <p className="app-section-meta">USD</p>
              <p className="app-stock-card-price" data-testid="assets-total-usd">
                {formatCurrency(totalUsd, "USD")}
              </p>
              <p className="app-section-meta">{t("portfolio.aggregate.combinedUsd")}</p>
              <p className="app-portfolio-combined-amount" data-testid="assets-combined-usd">
                {formatCurrency(combinedUsd, "USD")}
              </p>
            </div>
            <div className="app-portfolio-aggregate-card">
              <p className="app-section-meta">TWD</p>
              <p className="app-stock-card-price" data-testid="assets-total-twd">
                {formatCurrency(totalTwd, "TWD")}
              </p>
              <p className="app-section-meta">{t("portfolio.aggregate.combinedTwd")}</p>
              <p className="app-portfolio-combined-amount" data-testid="assets-combined-twd">
                {formatCurrency(combinedTwd, "TWD")}
              </p>
            </div>
          </div>
          <p className="app-section-meta">
            {t("portfolio.aggregate.fxRate")}:{" "}
            {usdTwdRate > 0 ? `1 USD = ${usdTwdRate.toFixed(4)} TWD` : "—"}
          </p>
        </Tile>
      </Column>

      <Column className="app-column-spacing" sm={4} md={8} lg={16}>
        <Tile className="app-section-tile">
          <div className="app-portfolio-asset-form">
            <Select
              id="portfolio-asset-type"
              labelText={t("portfolio.assets.column.type")}
              value={assetTypeDraft}
              onChange={(event) => setAssetTypeDraft(event.currentTarget.value)}
            >
              <SelectItem value="cash" text={t("portfolio.assets.type.cash")} />
              <SelectItem value="real_estate" text={t("portfolio.assets.type.realEstate")} />
              <SelectItem value="other" text={t("portfolio.assets.type.other")} />
            </Select>
            <TextInput
              id="portfolio-asset-name"
              labelText={t("portfolio.assets.column.name")}
              value={assetNameDraft}
              onChange={(event) => setAssetNameDraft(event.currentTarget.value)}
            />
            <Select
              id="portfolio-asset-currency"
              labelText={t("portfolio.assets.column.currency")}
              value={assetCurrencyDraft}
              onChange={(event) => setAssetCurrencyDraft(event.currentTarget.value)}
            >
              <SelectItem value="USD" text="USD" />
              <SelectItem value="TWD" text="TWD" />
            </Select>
            <TextInput
              id="portfolio-asset-value"
              labelText={t("portfolio.assets.column.value")}
              value={assetValueDraft}
              type="number"
              min={0}
              step="any"
              onChange={(event) => setAssetValueDraft(event.currentTarget.value)}
            />
            <Button
              data-testid="asset-add-button"
              onClick={handleAddAsset}
              disabled={addAssetMutation.isPending}
            >
              {t("portfolio.assets.add")}
            </Button>
          </div>
          {assetsQuery.isLoading ? (
            <InlineLoading description={t("portfolio.assets.loading")} status="active" />
          ) : assets.length === 0 ? (
            <p className="app-supporting-copy">{t("portfolio.assets.empty")}</p>
          ) : (
            <div className="app-assets-currency-columns">
              <TableContainer title="USD" className="app-portfolio-table-wrap app-asset-table-wrap">
                <Table size="lg" useZebraStyles>
                  <TableHead>
                    <TableRow>
                      <TableHeader>{t("portfolio.assets.column.type")}</TableHeader>
                      <TableHeader>{t("portfolio.assets.column.name")}</TableHeader>
                      <TableHeader>{t("portfolio.assets.column.value")}</TableHeader>
                      <TableHeader>{t("portfolio.column.actions")}</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {usdAssets.map((asset) => (
                      <TableRow key={asset.id}>
                        <TableCell>{asset.assetType}</TableCell>
                        <TableCell>{asset.name}</TableCell>
                        <TableCell>{formatCurrency(Number(asset.value), "USD")}</TableCell>
                        <TableCell>
                          <Button
                            data-testid={`asset-remove-${asset.id}`}
                            kind="ghost"
                            size="sm"
                            onClick={() => deleteAssetMutation.mutate(asset.id)}
                            disabled={deleteAssetMutation.isPending}
                          >
                            {t("portfolio.assets.remove")}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <TableContainer title="TWD" className="app-portfolio-table-wrap app-asset-table-wrap">
                <Table size="lg" useZebraStyles>
                  <TableHead>
                    <TableRow>
                      <TableHeader>{t("portfolio.assets.column.type")}</TableHeader>
                      <TableHeader>{t("portfolio.assets.column.name")}</TableHeader>
                      <TableHeader>{t("portfolio.assets.column.value")}</TableHeader>
                      <TableHeader>{t("portfolio.column.actions")}</TableHeader>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {twdAssets.map((asset) => (
                      <TableRow key={asset.id}>
                        <TableCell>{asset.assetType}</TableCell>
                        <TableCell>{asset.name}</TableCell>
                        <TableCell>{formatCurrency(Number(asset.value), "TWD")}</TableCell>
                        <TableCell>
                          <Button
                            data-testid={`asset-remove-${asset.id}`}
                            kind="ghost"
                            size="sm"
                            onClick={() => deleteAssetMutation.mutate(asset.id)}
                            disabled={deleteAssetMutation.isPending}
                          >
                            {t("portfolio.assets.remove")}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </div>
          )}
        </Tile>
      </Column>
    </Grid>
  );
}
