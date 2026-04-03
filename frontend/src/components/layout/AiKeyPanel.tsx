"use client";

import { Button, PasswordInput, Select, SelectItem } from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { fetchApiKeyStatus, saveApiKey } from "../../services/api/client";

type Provider = "openai" | "perplexity";

const PROVIDER_LABEL: Record<Provider, string> = {
  openai: "OpenAI",
  perplexity: "Perplexity",
};

export function AiKeyPanel() {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<Provider>("openai");
  const [input, setInput] = useState("");
  const openAiStatusQuery = useQuery({
    queryKey: ["api-key-status", "openai"],
    queryFn: () => fetchApiKeyStatus("openai"),
  });
  const perplexityStatusQuery = useQuery({
    queryKey: ["api-key-status", "perplexity"],
    queryFn: () => fetchApiKeyStatus("perplexity"),
  });
  const saveMutation = useMutation({
    mutationFn: (apiKey: string) => saveApiKey(provider, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["api-key-status", provider] });
      setInput("");
    },
  });

  const selectedStatus = provider === "openai" ? openAiStatusQuery.data : perplexityStatusQuery.data;
  const statusText = selectedStatus?.configured
    ? `Configured for ${PROVIDER_LABEL[provider]} (****${selectedStatus.keyLast4 ?? ""})`
    : `Not configured for ${PROVIDER_LABEL[provider]}`;

  return (
    <div className="app-ai-key-panel">
      <p className="app-eyebrow">AI providers</p>
      <p className="app-shell-brand-subtitle">
        Set either OpenAI or Perplexity API key to use your own AI subscription.
      </p>
      <Select
        id="api-provider-select"
        labelText="Provider"
        value={provider}
        onChange={(event) => setProvider(event.target.value as Provider)}
      >
        <SelectItem value="openai" text="OpenAI" />
        <SelectItem value="perplexity" text="Perplexity" />
      </Select>
      <p className="app-section-meta">
        {openAiStatusQuery.isLoading || perplexityStatusQuery.isLoading ? "Checking..." : statusText}
      </p>
      <PasswordInput
        id={`api-key-${provider}`}
        labelText={`${PROVIDER_LABEL[provider]} API key`}
        value={input}
        onChange={(event) => setInput(event.target.value)}
        placeholder="Paste API key"
      />
      <Button
        size="sm"
        kind="secondary"
        disabled={!input.trim() || saveMutation.isPending}
        onClick={() => saveMutation.mutate(input)}
      >
        {saveMutation.isPending ? "Saving..." : "Save key"}
      </Button>
      {saveMutation.error ? (
        <p className="app-supporting-copy app-negative">
          {(saveMutation.error as Error).message}
        </p>
      ) : null}
    </div>
  );
}
