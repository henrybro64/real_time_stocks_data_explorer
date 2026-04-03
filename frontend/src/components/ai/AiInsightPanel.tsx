"use client";

import { Button, InlineLoading, Select, SelectItem, Tile } from "@carbon/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { fetchAiInsight, fetchApiKeyStatus } from "../../services/api/client";

type Props = {
  stockId: string;
};

export function AiInsightPanel({ stockId }: Props) {
  const [provider, setProvider] = useState<"openai" | "perplexity">("openai");
  const [trigger, setTrigger] = useState(0);
  const openAiStatusQuery = useQuery({
    queryKey: ["api-key-status", "openai"],
    queryFn: () => fetchApiKeyStatus("openai"),
  });
  const perplexityStatusQuery = useQuery({
    queryKey: ["api-key-status", "perplexity"],
    queryFn: () => fetchApiKeyStatus("perplexity"),
  });
  const availableProviders = useMemo(() => {
    const items: Array<"openai" | "perplexity"> = [];
    if (openAiStatusQuery.data?.configured) {
      items.push("openai");
    }
    if (perplexityStatusQuery.data?.configured) {
      items.push("perplexity");
    }
    return items;
  }, [openAiStatusQuery.data?.configured, perplexityStatusQuery.data?.configured]);

  useEffect(() => {
    if (availableProviders.length === 0) {
      return;
    }
    if (!availableProviders.includes(provider)) {
      setProvider(availableProviders[0]);
    }
  }, [availableProviders, provider]);

  const insightQuery = useQuery({
    queryKey: ["ai-insight", stockId, provider, trigger],
    queryFn: () => fetchAiInsight(stockId, provider),
    enabled: trigger > 0,
    retry: false,
  });

  return (
    <Tile className="app-section-tile">
      <div className="app-section-header">
        <div>
          <p className="app-eyebrow">AI</p>
          <h2>AI insight</h2>
        </div>
        <p className="app-section-meta">Uses your saved provider API key from AI Settings.</p>
      </div>
      <div className="app-ai-insight-controls">
        <Select
          id="ai-provider"
          labelText="Provider"
          value={provider}
          onChange={(event) => setProvider(event.target.value as "openai" | "perplexity")}
        >
          <SelectItem value="openai" text="OpenAI" disabled={!openAiStatusQuery.data?.configured} />
          <SelectItem value="perplexity" text="Perplexity" disabled={!perplexityStatusQuery.data?.configured} />
        </Select>
        <Button
          size="sm"
          disabled={availableProviders.length === 0}
          onClick={() => setTrigger((value) => value + 1)}
        >
          AI Analysis
        </Button>
      </div>
      {availableProviders.length === 0 ? (
        <p className="app-supporting-copy">
          Configure either OpenAI or Perplexity key in AI Settings to enable AI output.
        </p>
      ) : null}
      {insightQuery.isFetching ? <InlineLoading description="Generating AI insight..." status="active" /> : null}
      {insightQuery.error ? (
        <p className="app-supporting-copy app-negative">Failed to load AI insight. Check provider key/settings.</p>
      ) : null}
      {insightQuery.data ? <p className="app-supporting-copy app-ai-insight-output">{insightQuery.data.content}</p> : null}
    </Tile>
  );
}
