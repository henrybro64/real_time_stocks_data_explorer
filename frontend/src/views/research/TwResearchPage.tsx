"use client";

import {
  Button,
  Column,
  Grid,
  InlineLoading,
  Pagination,
  Select,
  SelectItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableHeader,
  TableRow,
  TextArea,
  TextInput,
  Tile,
} from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { useLanguage } from "../../app/i18n";
import { RequestStateNotice, errorToNoticeKind } from "../../components/state/RequestStateNotice";
import { toApiError } from "../../services/api/client";
import {
  createTwResearchScan,
  fetchThemeOptions,
  fetchTwResearchBrowserText,
  fetchTwResearchExportChunk,
  fetchTwResearchExportManifest,
  fetchTwResearchScan,
  resolveTwResearchUniverse,
  runTwResearchBatch,
  runTwResearchEnrichmentBatch,
  twResearchCsvUrl,
  type TwResearchScanSnapshot,
  type TwResearchUniverseMode,
} from "../../services/api/twResearch";

const PAGE_SIZE = 25;

function splitLines(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((value) => value.trim())
    .filter(Boolean);
}

export function TwResearchPage() {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<TwResearchUniverseMode>("starter_preset");
  const [stockIdsText, setStockIdsText] = useState("TW:2727\nTW:2618\nTW:2330");
  const [themeKeysText, setThemeKeysText] = useState("tw-pcb\ntw-ccl");
  const [targetCount, setTargetCount] = useState<10 | 20>(10);
  const [scanId, setScanId] = useState<string | null>(null);
  const [scan, setScan] = useState<TwResearchScanSnapshot | null>(null);
  const [page, setPage] = useState(1);
  const [agentText, setAgentText] = useState("");
  const [agentChunkIndex, setAgentChunkIndex] = useState(0);
  const [agentTotalChunks, setAgentTotalChunks] = useState(0);

  const refreshManifest = (id: string | null) => {
    if (!id) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["tw-research-manifest", id] });
  };

  const requireScanId = () => {
    if (!scanId) {
      throw new Error("missing scan");
    }
    return scanId;
  };

  const applyScanSnapshot = (next: TwResearchScanSnapshot) => {
    setScan(next);
    refreshManifest(next.scanId);
  };

  const explicitIds = useMemo(() => splitLines(stockIdsText), [stockIdsText]);
  const themeKeys = useMemo(() => splitLines(themeKeysText), [themeKeysText]);

  const themesQuery = useQuery({
    queryKey: ["themes-for-tw-research"],
    queryFn: fetchThemeOptions,
  });

  const twThemeHint = useMemo(() => {
    const keys = (themesQuery.data ?? [])
      .map((theme) => theme.key)
      .filter((key) => key.startsWith("tw-"));
    return keys.length ? keys.join(", ") : "tw-pcb, tw-ccl, …";
  }, [themesQuery.data]);

  const manifestQuery = useQuery({
    queryKey: ["tw-research-manifest", scanId],
    queryFn: () => fetchTwResearchExportManifest(scanId as string),
    enabled: Boolean(scanId),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      if (mode === "stock_ids") {
        await resolveTwResearchUniverse({ mode, stockIds: explicitIds });
      } else if (mode === "theme_keys") {
        await resolveTwResearchUniverse({ mode, themeKeys });
      }
      return createTwResearchScan({
        mode,
        stockIds: mode === "stock_ids" ? explicitIds : [],
        themeKeys: mode === "theme_keys" ? themeKeys : [],
        targetCount,
      });
    },
    onSuccess: (created) => {
      setScanId(created.scanId);
      setScan(created);
      setPage(1);
      setAgentText("");
      setAgentChunkIndex(0);
      setAgentTotalChunks(0);
      refreshManifest(created.scanId);
    },
  });

  const batchMutation = useMutation({
    mutationFn: async () => runTwResearchBatch(requireScanId(), 10),
    onSuccess: applyScanSnapshot,
  });

  const enrichMutation = useMutation({
    mutationFn: async () => runTwResearchEnrichmentBatch(requireScanId(), 10),
    onSuccess: applyScanSnapshot,
  });

  const refreshMutation = useMutation({
    mutationFn: async () => fetchTwResearchScan(requireScanId()),
    onSuccess: applyScanSnapshot,
  });

  const agentMutation = useMutation({
    mutationFn: async (chunkIndex: number) =>
      fetchTwResearchBrowserText(requireScanId(), chunkIndex),
    onSuccess: (payload, chunkIndex) => {
      setAgentChunkIndex(chunkIndex);
      const continuation = payload.continuation as
        | { totalChunks?: number; nextChunkIndex?: number | null }
        | undefined;
      setAgentTotalChunks(Number(continuation?.totalChunks ?? 0));
      setAgentText(JSON.stringify(payload, null, 2));
    },
  });

  const ledger = (scan?.ledger ?? []) as Array<Record<string, unknown>>;
  const pageRows = ledger.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const busy =
    createMutation.isPending ||
    batchMutation.isPending ||
    enrichMutation.isPending ||
    refreshMutation.isPending ||
    agentMutation.isPending;
  const error =
    createMutation.error ||
    batchMutation.error ||
    enrichMutation.error ||
    refreshMutation.error ||
    agentMutation.error ||
    manifestQuery.error;

  return (
    <Grid className="app-page-grid" fullWidth>
      <Column lg={16} md={8} sm={4}>
        <Stack gap={6}>
          <div>
            <p className="cds--label-01">{t("research.tw.eyebrow")}</p>
            <h1 className="cds--productive-heading-04">{t("research.tw.title")}</h1>
            <p className="cds--body-compact-01">{t("research.tw.subtitle")}</p>
          </div>

          {error ? (
            <RequestStateNotice
              kind={errorToNoticeKind(toApiError(error))}
              scopeLabel={t("research.tw.errorTitle")}
              detail={toApiError(error).detail || toApiError(error).message}
            />
          ) : null}

          <Tile>
            <Stack gap={4}>
              <Select
                id="tw-research-mode"
                labelText={t("research.tw.universeMode")}
                value={mode}
                onChange={(event) => setMode(event.target.value as TwResearchUniverseMode)}
              >
                <SelectItem value="starter_preset" text={t("research.tw.modePreset")} />
                <SelectItem value="theme_keys" text={t("research.tw.modeThemes")} />
                <SelectItem value="stock_ids" text={t("research.tw.modeIds")} />
              </Select>
              {mode === "stock_ids" ? (
                <TextArea
                  id="tw-research-stock-ids"
                  labelText={t("research.tw.stockIds")}
                  value={stockIdsText}
                  onChange={(event) => setStockIdsText(event.target.value)}
                  rows={4}
                />
              ) : null}
              {mode === "theme_keys" ? (
                <Stack gap={2}>
                  <TextArea
                    id="tw-research-theme-keys"
                    labelText={t("research.tw.themeKeys")}
                    value={themeKeysText}
                    onChange={(event) => setThemeKeysText(event.target.value)}
                    rows={4}
                  />
                  <p className="cds--helper-text-01">
                    {t("research.tw.themeKeysHint", { keys: twThemeHint })}
                  </p>
                </Stack>
              ) : null}
              <Select
                id="tw-research-target"
                labelText={t("research.tw.targetCount")}
                value={String(targetCount)}
                onChange={(event) => setTargetCount(Number(event.target.value) as 10 | 20)}
              >
                <SelectItem value="10" text="10" />
                <SelectItem value="20" text="20" />
              </Select>
              <Stack gap={3} orientation="horizontal">
                {(
                  [
                    {
                      key: "create",
                      kind: "primary" as const,
                      label: "research.tw.createScan" as const,
                      disabled: busy,
                      onClick: () => createMutation.mutate(),
                    },
                    {
                      key: "batch",
                      kind: "secondary" as const,
                      label: "research.tw.runBatch" as const,
                      disabled: busy || !scanId,
                      onClick: () => batchMutation.mutate(),
                    },
                    {
                      key: "enrich",
                      kind: "tertiary" as const,
                      label: "research.tw.runEnrichment" as const,
                      disabled: busy || !scanId,
                      onClick: () => enrichMutation.mutate(),
                    },
                    {
                      key: "refresh",
                      kind: "ghost" as const,
                      label: "research.tw.refresh" as const,
                      disabled: busy || !scanId,
                      onClick: () => refreshMutation.mutate(),
                    },
                  ]
                ).map((action) => (
                  <Button
                    key={action.key}
                    size="md"
                    kind={action.kind}
                    disabled={action.disabled}
                    onClick={action.onClick}
                  >
                    {t(action.label)}
                  </Button>
                ))}
              </Stack>
              {busy ? <InlineLoading description={t("research.tw.working")} /> : null}
            </Stack>
          </Tile>

          {scan ? (
            <Tile>
              <Stack gap={3}>
                <TextInput
                  id="tw-research-scan-id"
                  labelText={t("research.tw.scanId")}
                  value={scan.scanId}
                  readOnly
                />
                <p className="cds--body-compact-01">
                  {t("research.tw.statusLine", {
                    status: scan.status,
                    processed: scan.processedCount,
                    universe: scan.universeSize,
                    remaining: scan.remainingCount,
                    dayPass: scan.dayPassCount,
                    qualified: scan.qualifiedCount ?? 0,
                    target: scan.targetCount,
                  })}
                </p>
                <p className="cds--helper-text-01">{t("research.tw.targetNote")}</p>
                {manifestQuery.data ? (
                  <pre className="cds--body-compact-01" style={{ whiteSpace: "pre-wrap" }}>
                    {JSON.stringify(manifestQuery.data, null, 2)}
                  </pre>
                ) : null}
              </Stack>
            </Tile>
          ) : null}

          {scan ? (
            <TableContainer title={t("research.tw.ledgerTitle")}>
              <Table size="sm">
                <TableHead>
                  <TableRow>
                    <TableHeader>{t("research.tw.colStock")}</TableHeader>
                    <TableHeader>{t("research.tw.colStage")}</TableHeader>
                    <TableHeader>{t("research.tw.colDaily")}</TableHeader>
                    <TableHeader>{t("research.tw.colMonthly")}</TableHeader>
                    <TableHeader>{t("research.tw.colThemes")}</TableHeader>
                    <TableHeader>{t("research.tw.colReason")}</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {pageRows.map((row) => {
                    const daily = (row.daily as Record<string, unknown> | undefined) || {};
                    const monthly = (row.monthly as Record<string, unknown> | undefined) || {};
                    const themes = (row.theme_keys as string[] | undefined) || [];
                    return (
                      <TableRow key={String(row.stock_id)}>
                        <TableCell>{String(row.stock_id)}</TableCell>
                        <TableCell>{String(row.stage)}</TableCell>
                        <TableCell>{String(daily.passStatus ?? "")}</TableCell>
                        <TableCell>{String(monthly.passStatus ?? "")}</TableCell>
                        <TableCell>
                          {themes.length ? themes.join(", ") : t("research.tw.unclassified")}
                        </TableCell>
                        <TableCell>{String(row.rejection_reason ?? "")}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                pageSizes={[PAGE_SIZE]}
                totalItems={ledger.length}
                onChange={({ page: nextPage }) => setPage(nextPage)}
              />
            </TableContainer>
          ) : null}

          {scanId ? (
            <Tile>
              <Stack gap={4}>
                <Stack gap={3} orientation="horizontal">
                  <Button
                    size="md"
                    kind="secondary"
                    disabled={busy}
                    onClick={() => agentMutation.mutate(0)}
                  >
                    {t("research.tw.loadAgentJson")}
                  </Button>
                  <Button
                    size="md"
                    kind="ghost"
                    disabled={busy || agentTotalChunks <= 0 || agentChunkIndex <= 0}
                    onClick={() => agentMutation.mutate(Math.max(0, agentChunkIndex - 1))}
                  >
                    {t("research.tw.prevChunk")}
                  </Button>
                  <Button
                    size="md"
                    kind="ghost"
                    disabled={
                      busy ||
                      agentTotalChunks <= 0 ||
                      agentChunkIndex + 1 >= agentTotalChunks
                    }
                    onClick={() => agentMutation.mutate(agentChunkIndex + 1)}
                  >
                    {t("research.tw.nextChunk")}
                  </Button>
                  <Button
                    size="md"
                    kind="tertiary"
                    disabled={busy}
                    onClick={async () => {
                      if (!scanId) {
                        return;
                      }
                      const chunk = await fetchTwResearchExportChunk(scanId, agentChunkIndex);
                      setAgentText(JSON.stringify(chunk, null, 2));
                    }}
                  >
                    {t("research.tw.loadChunkOnly")}
                  </Button>
                  <Button size="md" kind="tertiary" disabled={busy}>
                    <a href={twResearchCsvUrl(scanId)}>{t("research.tw.downloadCsv")}</a>
                  </Button>
                </Stack>
                <p className="cds--helper-text-01">
                  {t("research.tw.agentChunkStatus", {
                    index: agentChunkIndex,
                    total: agentTotalChunks || "—",
                  })}
                </p>
                <p className="cds--helper-text-01">{t("research.tw.agentHandoffNote")}</p>
                <TextArea
                  id="tw-research-agent-json"
                  labelText={t("research.tw.agentJson")}
                  value={agentText}
                  readOnly
                  rows={16}
                />
              </Stack>
            </Tile>
          ) : null}
        </Stack>
      </Column>
    </Grid>
  );
}
