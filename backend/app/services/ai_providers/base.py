from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol


SYSTEM_PROMPT = (
    "You are a careful financial analysis assistant. "
    "Use the provided computed stock metrics as the source of truth. "
    "Do not give buy, sell, or hold recommendations. "
    "Do not predict exact future stock prices. "
    "Explain technical trend, momentum, risk, valuation context, and data limitations. "
    "If recent news context is available, include it. "
    "If recent news context is not available, clearly say the analysis is based only on "
    "computed historical price and valuation data. "
    "Keep the language clear for a non-professional investor. "
    "This is for informational and educational purposes only, not financial advice. "
    "Return sections for: short summary; technical trend analysis; momentum and risk analysis; "
    "valuation context; recent news or market context, if available; what to watch next; "
    "data limitations; informational-only disclaimer."
)


@dataclass(frozen=True)
class AnalysisCitation:
    url: str
    title: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class AnalysisSearchResult:
    title: str
    url: str
    source: str | None = None


@dataclass(frozen=True)
class AIAnalysisResult:
    provider: str
    model: str
    content: str
    citations: tuple[AnalysisCitation, ...] = ()
    search_results: tuple[AnalysisSearchResult, ...] = ()


class AIProviderError(RuntimeError):
    pass


class AIProviderConfigurationError(AIProviderError):
    pass


class AIProvider(Protocol):
    provider_name: str
    model_name: str

    def generate_stock_analysis(
        self,
        input_data: Mapping[str, object],
    ) -> AIAnalysisResult: ...


def build_stock_analysis_user_prompt(input_data: Mapping[str, object]) -> str:
    payload = json.dumps(input_data, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "Analyze the stock using only the computed JSON summary below as the source of truth.\n"
        "If your runtime cannot verify recent news, explicitly say so.\n\n"
        f"{payload}"
    )
