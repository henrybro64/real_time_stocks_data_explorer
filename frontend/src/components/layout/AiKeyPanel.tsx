"use client";

import { Button, PasswordInput, Select, SelectItem } from "@carbon/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useLanguage } from "../../app/i18n";
import {
  fetchAiProviderPreference,
  fetchApiKeyStatus,
  saveAiProviderPreference,
  saveApiKey,
} from "../../services/api/client";

type Provider = "openai" | "perplexity";

const PROVIDER_LABEL: Record<Provider, string> = {
  openai: "OpenAI",
  perplexity: "Perplexity",
};

export function AiKeyPanel() {
  const { t } = useLanguage();
  const queryClient = useQueryClient();
  const [keyProvider, setKeyProvider] = useState<Provider>("openai");
  const [preferredProvider, setPreferredProvider] = useState<Provider>("openai");
  const [input, setInput] = useState("");

  const preferenceQuery = useQuery({
    queryKey: ["ai-provider-preference"],
    queryFn: fetchAiProviderPreference,
  });

  useEffect(() => {
    const stored = preferenceQuery.data?.preferredProvider;
    if (stored === "openai" || stored === "perplexity") {
      setPreferredProvider(stored);
    }
  }, [preferenceQuery.data?.preferredProvider]);

  const openAiStatusQuery = useQuery({
    queryKey: ["api-key-status", "openai"],
    queryFn: () => fetchApiKeyStatus("openai"),
  });
  const perplexityStatusQuery = useQuery({
    queryKey: ["api-key-status", "perplexity"],
    queryFn: () => fetchApiKeyStatus("perplexity"),
  });

  const preferenceMutation = useMutation({
    mutationFn: () => saveAiProviderPreference(preferredProvider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ai-provider-preference"] });
      queryClient.invalidateQueries({ queryKey: ["stock-analysis"] });
    },
  });

  const saveMutation = useMutation({
    mutationFn: (apiKey: string) => saveApiKey(keyProvider, apiKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["api-key-status", keyProvider] });
      queryClient.invalidateQueries({ queryKey: ["ai-provider-preference"] });
      queryClient.invalidateQueries({ queryKey: ["stock-analysis"] });
      setInput("");
    },
  });

  const selectedStatus = keyProvider === "openai" ? openAiStatusQuery.data : perplexityStatusQuery.data;
  const statusText = selectedStatus?.configured
    ? t("aiSettings.keyConfigured").replace("{provider}", PROVIDER_LABEL[keyProvider]).replace(
        "{last4}",
        selectedStatus.keyLast4 ?? "",
      )
    : t("aiSettings.keyNotConfigured").replace("{provider}", PROVIDER_LABEL[keyProvider]);

  const activeLabel =
    preferenceQuery.data?.preferredProvider != null
      ? PROVIDER_LABEL[preferenceQuery.data.preferredProvider as Provider]
      : t("aiSettings.activeProviderAuto");

  return (
    <div className="app-ai-key-panel">
      <p className="app-eyebrow">{t("aiSettings.providersEyebrow")}</p>
      <p className="app-shell-brand-subtitle">{t("aiSettings.providersIntro")}</p>

      <div className="app-ai-key-section">
        <p className="app-section-meta app-ai-key-section-title">{t("aiSettings.activeProviderTitle")}</p>
        <p className="app-supporting-copy">{t("aiSettings.activeProviderHelp")}</p>
        <Select
          id="ai-preferred-provider-select"
          labelText={t("aiSettings.activeProviderLabel")}
          value={preferredProvider}
          onChange={(event) => setPreferredProvider(event.target.value as Provider)}
        >
          <SelectItem value="openai" text="OpenAI" />
          <SelectItem value="perplexity" text="Perplexity" />
        </Select>
        <p className="app-section-meta">
          {preferenceQuery.isLoading
            ? t("aiSettings.checking")
            : t("aiSettings.activeProviderCurrent").replace("{provider}", activeLabel)}
        </p>
        <Button
          size="sm"
          kind="primary"
          disabled={preferenceMutation.isPending}
          onClick={() => preferenceMutation.mutate()}
        >
          {preferenceMutation.isPending ? t("aiSettings.saving") : t("aiSettings.saveActiveProvider")}
        </Button>
        {preferenceMutation.error ? (
          <p className="app-supporting-copy app-negative">{(preferenceMutation.error as Error).message}</p>
        ) : null}
      </div>

      <div className="app-ai-key-section">
        <p className="app-section-meta app-ai-key-section-title">{t("aiSettings.apiKeysTitle")}</p>
        <Select
          id="api-provider-select"
          labelText={t("aiSettings.keyProviderLabel")}
          value={keyProvider}
          onChange={(event) => setKeyProvider(event.target.value as Provider)}
        >
          <SelectItem value="openai" text="OpenAI" />
          <SelectItem value="perplexity" text="Perplexity" />
        </Select>
        <p className="app-section-meta">
          {openAiStatusQuery.isLoading || perplexityStatusQuery.isLoading
            ? t("aiSettings.checking")
            : statusText}
        </p>
        <p className="app-supporting-copy">
          {t("aiSettings.openAiStatus")}:{" "}
          {openAiStatusQuery.data?.configured
            ? `****${openAiStatusQuery.data.keyLast4 ?? ""}`
            : t("aiSettings.notConfiguredShort")}
          {" · "}
          {t("aiSettings.perplexityStatus")}:{" "}
          {perplexityStatusQuery.data?.configured
            ? `****${perplexityStatusQuery.data.keyLast4 ?? ""}`
            : t("aiSettings.notConfiguredShort")}
        </p>
        <PasswordInput
          id={`api-key-${keyProvider}`}
          labelText={t("aiSettings.apiKeyLabel").replace("{provider}", PROVIDER_LABEL[keyProvider])}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={t("aiSettings.apiKeyPlaceholder")}
        />
        <Button
          size="sm"
          kind="secondary"
          disabled={!input.trim() || saveMutation.isPending}
          onClick={() => saveMutation.mutate(input)}
        >
          {saveMutation.isPending ? t("aiSettings.saving") : t("aiSettings.saveApiKey")}
        </Button>
        {saveMutation.error ? (
          <p className="app-supporting-copy app-negative">{(saveMutation.error as Error).message}</p>
        ) : null}
      </div>
    </div>
  );
}
