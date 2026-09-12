from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import logging
import re
from time import perf_counter
from typing import Callable, cast

import pandas as pd

from app.core.diagnostics import elapsed_ms
from app.models.quote import CandleRecord
from app.models.stock import StockRecord
from app.services.ai_providers import (
    AIProvider,
    AIProviderConfigurationError,
    AIProviderError,
    AnalysisCitation,
    AnalysisSearchResult,
    create_ai_provider,
)
from app.services.moving_average import history_fetch_limit
from app.services.news_service import AnalysisNewsContext, select_news_for_analysis
from app.services.quote_service import quote_service
from app.services.stock_analysis_fundamental import build_fundamental_outlook
from app.services.stock_analysis_institutional import build_institutional_analysis
from app.services.stock_analysis_language import (
    DEFAULT_ANALYSIS_LANGUAGE,
    default_analysis_language_for_market,
    normalize_analysis_language,
)
from app.services.stock_analysis_metrics import (
    calculate_annualized_volatility,
    calculate_average_volume,
    calculate_max_drawdown,
    calculate_price_vs_average_percent,
    calculate_return_percent,
    calculate_rsi,
    calculate_simple_moving_average,
    clean_numeric_series,
    select_price_series,
)
from app.services.stock_analysis_technical import (
    build_technical_analysis,
    completed_session_history,
)

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised through runtime config paths.
    yf = None


HistoryLoader = Callable[[str], pd.DataFrame]
InfoLoader = Callable[[str], dict[str, object]]
ProviderFactory = Callable[[], AIProvider | None]
NewsSelector = Callable[[StockRecord], AnalysisNewsContext]

RETURN_WINDOWS = {
    "return1mPercent": 21,
    "return3mPercent": 63,
    "return6mPercent": 126,
    "return1yPercent": 252,
}

HISTORY_FETCH_ERROR = "Failed to load 1 year of daily stock history from yfinance."
NO_HISTORY_ERROR = "No historical price data returned from yfinance."
UNUSABLE_PRICE_HISTORY_ERROR = (
    "Historical price data from yfinance did not include usable adjusted or closing prices."
)
METADATA_LOAD_WARNING = "Some company metadata could not be loaded from yfinance."
INVALID_YFINANCE_TICKER_ERROR = "Ticker appears invalid or unavailable in yfinance."
SKIP_AI_MISSING_HISTORY_WARNING = (
    "Skipping AI analysis because required yfinance historical price data is unavailable."
)
NEWS_CONTEXT_WARNING = "Recent news context could not be loaded for this analysis."

# Citation dedupe compares URLs ignoring scheme and a leading "www." host label.
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_URL_HOST_PREFIX_RE = re.compile(r"^www\.")

logger = logging.getLogger(__name__)


def _empty_price_history_frame() -> pd.DataFrame:
    """Stable empty frame; bare ``pd.DataFrame()`` can break some numpy/pandas builds."""
    return pd.DataFrame(columns=["Close", "Adj Close", "Volume"])


def _coerce_history_frame(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty and history.columns.size == 0:
        return _empty_price_history_frame()
    return history


@dataclass(frozen=True)
class ComputedStockAnalysisData:
    ticker: str
    company_name: str | None
    currency: str | None
    sector: str | None
    industry: str | None
    market_cap: int | None
    trailing_pe: float | None
    forward_pe: float | None
    current_price: float | None
    return_1m_percent: float | None
    return_3m_percent: float | None
    return_6m_percent: float | None
    return_1y_percent: float | None
    sma_50: float | None
    sma_200: float | None
    price_vs_sma_50_percent: float | None
    price_vs_sma_200_percent: float | None
    rsi_14: float | None
    volatility_30d_annualized: float | None
    max_drawdown_1y_percent: float | None
    average_volume_30d: int | None
    technical_analysis: dict[str, object] | None = None
    institutional_analysis: dict[str, object] | None = None
    fundamental_outlook: dict[str, object] | None = None
    recent_news_context: dict[str, object] | None = None

    def to_prompt_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ticker": self.ticker,
            "companyName": self.company_name,
            "currency": self.currency,
            "sector": self.sector,
            "industry": self.industry,
            "marketCap": self.market_cap,
            "trailingPe": self.trailing_pe,
            "forwardPe": self.forward_pe,
            "currentPrice": self.current_price,
            "return1mPercent": self.return_1m_percent,
            "return3mPercent": self.return_3m_percent,
            "return6mPercent": self.return_6m_percent,
            "return1yPercent": self.return_1y_percent,
            "sma50": self.sma_50,
            "sma200": self.sma_200,
            "priceVsSma50Percent": self.price_vs_sma_50_percent,
            "priceVsSma200Percent": self.price_vs_sma_200_percent,
            "rsi14": self.rsi_14,
            "volatility30dAnnualized": self.volatility_30d_annualized,
            "maxDrawdown1yPercent": self.max_drawdown_1y_percent,
            "averageVolume30d": self.average_volume_30d,
        }
        if self.technical_analysis is not None:
            payload["technicalAnalysis"] = self.technical_analysis
        if self.institutional_analysis is not None:
            payload["institutionalAnalysis"] = self.institutional_analysis
        if self.fundamental_outlook is not None:
            payload["fundamentalOutlook"] = self.fundamental_outlook
        if self.recent_news_context is not None:
            payload["recentNewsContext"] = self.recent_news_context
        return payload


@dataclass(frozen=True)
class StockAnalysisResult:
    stock_id: str
    ticker: str
    computed_data: ComputedStockAnalysisData
    ai_provider: str | None
    ai_model: str | None
    ai_analysis: str | None
    citations: tuple[AnalysisCitation, ...] = ()
    search_results: tuple[AnalysisSearchResult, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    analyzed_at: datetime | None = None
    language: str = DEFAULT_ANALYSIS_LANGUAGE


class StockAnalysisService:
    def __init__(
        self,
        history_loader: HistoryLoader | None = None,
        info_loader: InfoLoader | None = None,
        provider_factory: ProviderFactory = create_ai_provider,
        news_selector: NewsSelector | None = None,
    ) -> None:
        # Injected loaders (tests) skip the chart provider so fixtures stay in control.
        self._prefer_chart_history = history_loader is None
        self._history_loader = history_loader or _load_history_from_yfinance
        self._info_loader = info_loader or _load_info_from_yfinance
        self._provider_factory = provider_factory
        self._news_selector = news_selector or select_news_for_analysis

    def analyze_stock(
        self,
        stock_id: str,
        *,
        provider_override: AIProvider | None = None,
        run_ai: bool = True,
        language: str | None = None,
        include_news: bool = True,
    ) -> StockAnalysisResult:
        started_at = perf_counter()
        stock = quote_service.get_stock(stock_id)
        if stock is None:
            raise ValueError("Stock not found")
        # The analysis route always passes a language. Callers that cannot
        # (legacy /insights) keep the previous market-derived default.
        if language is None:
            normalized_language = default_analysis_language_for_market(stock.market)
        else:
            normalized_language = normalize_analysis_language(language)

        ticker = normalize_stock_id_to_yfinance(stock)
        warnings: list[str] = []
        errors: list[str] = []
        history = self._load_price_history(stock.id, ticker, errors)
        history = _coerce_history_frame(history)
        # Filter before the usable-history flag so a lone still-forming bar
        # cannot look like enough data to send to the model.
        filtered_history = _completed_sessions_or_raw(stock, history)
        if not history.empty and filtered_history.empty:
            errors.append(UNUSABLE_PRICE_HISTORY_ERROR)
        history = filtered_history
        info = self._load_info(ticker, warnings)
        has_price_history = _has_usable_price_history(history)
        if not has_price_history and not history.empty:
            errors.append(UNUSABLE_PRICE_HISTORY_ERROR)
        if (
            any(
                error in errors
                for error in (NO_HISTORY_ERROR, UNUSABLE_PRICE_HISTORY_ERROR)
            )
            and METADATA_LOAD_WARNING not in warnings
            and not _has_meaningful_yfinance_info(info)
        ):
            errors.append(INVALID_YFINANCE_TICKER_ERROR)

        ai_provider: str | None = None
        ai_model: str | None = None
        ai_analysis: str | None = None
        citations: tuple[AnalysisCitation, ...] = ()
        search_results: tuple[AnalysisSearchResult, ...] = ()

        provider: AIProvider | None = None
        provider_config_error: str | None = None
        if run_ai:
            if provider_override is not None:
                provider = provider_override
            else:
                try:
                    provider = self._provider_factory()
                except AIProviderConfigurationError as exc:
                    provider_config_error = str(exc)
                    provider = None

        # News is only useful when a model will actually read it. Skip the
        # Google News round-trip when refresh cannot generate a narrative.
        news_context: AnalysisNewsContext | None = None
        if include_news and provider is not None and has_price_history:
            news_context = self._select_news(stock, warnings)

        computed_data = _build_computed_data(
            stock,
            ticker,
            history,
            info,
            news_context=news_context,
        )

        if run_ai:
            if provider is None:
                if provider_config_error is not None:
                    warnings.append(provider_config_error)
                else:
                    warnings.append(
                        "AI provider is not configured. Returning computed stock metrics only.",
                    )
            else:
                ai_provider = getattr(provider, "provider_name", None)
                ai_model = getattr(provider, "model_name", None)
                if not has_price_history:
                    warnings.append(
                        SKIP_AI_MISSING_HISTORY_WARNING,
                    )
                else:
                    prompt_payload = computed_data.to_prompt_payload()
                    prompt_payload["stockId"] = stock_id
                    prompt_payload["market"] = stock.market
                    prompt_payload["language"] = normalized_language
                    if warnings:
                        prompt_payload["warnings"] = list(warnings)
                    if errors:
                        prompt_payload["errors"] = list(errors)
                    ai_started_at = perf_counter()
                    try:
                        gen_result = provider.generate_stock_analysis(prompt_payload)
                        ai_analysis = gen_result.content
                        citations = merge_news_citations(
                            gen_result.citations,
                            news_context,
                        )
                        search_results = gen_result.search_results
                        ai_provider = gen_result.provider
                        ai_model = gen_result.model
                        logger.info(
                            "AI stock analysis completed",
                            extra={
                                "event": "stock_analysis.ai.completed",
                                "stock_id": stock_id,
                                "provider": ai_provider,
                                "model": ai_model,
                                "language": normalized_language,
                                "duration_ms": elapsed_ms(ai_started_at),
                                "citation_count": len(citations),
                            },
                        )
                    except (AIProviderError, Exception) as exc:
                        logger.warning(
                            "AI stock analysis provider failed",
                            extra={
                                "event": "stock_analysis.ai.failed",
                                "stock_id": stock_id,
                                "provider": ai_provider,
                                "model": ai_model,
                                "language": normalized_language,
                                "duration_ms": elapsed_ms(ai_started_at),
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            },
                        )
                        warnings.append(
                            "AI analysis is temporarily unavailable, but computed stock metrics are still available.",
                        )

        result = StockAnalysisResult(
            stock_id=stock_id,
            ticker=ticker,
            computed_data=computed_data,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_analysis=ai_analysis,
            citations=citations,
            search_results=search_results,
            warnings=tuple(dict.fromkeys(warnings)),
            errors=tuple(dict.fromkeys(errors)),
            language=normalized_language,
        )
        logger.info(
            "Stock analysis completed",
            extra={
                "event": "stock_analysis.completed",
                "stock_id": stock_id,
                "ticker": ticker,
                "run_ai": run_ai,
                "language": normalized_language,
                "provider": result.ai_provider,
                "model": result.ai_model,
                "duration_ms": elapsed_ms(started_at),
                "warning_count": len(result.warnings),
                "error_count": len(result.errors),
                "ai_generated": bool(result.ai_analysis),
            },
        )
        return result

    def _select_news(
        self,
        stock: StockRecord,
        warnings: list[str],
    ) -> AnalysisNewsContext:
        try:
            return self._news_selector(stock)
        except Exception as exc:  # noqa: BLE001 — news is optional
            logger.warning(
                "News selection for analysis failed",
                extra={
                    "event": "stock_analysis.news.failed",
                    "stock_id": stock.id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            warnings.append(NEWS_CONTEXT_WARNING)
            return AnalysisNewsContext(
                available=False,
                window_start=None,
                window_end=None,
                data_as_of=None,
                items=(),
                failure_reason=f"selector_failed:{type(exc).__name__}",
            )

    def _load_price_history(
        self,
        stock_id: str,
        ticker: str,
        errors: list[str],
    ) -> pd.DataFrame:
        """Prefer the same candles the chart plots; fall back to yfinance."""
        if self._prefer_chart_history:
            chart_history = _history_from_chart_provider(stock_id)
            if chart_history is not None:
                return chart_history
        return self._load_history(ticker, errors)

    def _load_history(self, ticker: str, errors: list[str]) -> pd.DataFrame:
        try:
            history = self._history_loader(ticker)
        except Exception as exc:
            logger.warning(
                "yfinance history load failed",
                extra={
                    "event": "stock_analysis.history.failed",
                    "ticker": ticker,
                    "provider": "yfinance",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            errors.append(HISTORY_FETCH_ERROR)
            return _empty_price_history_frame()
        if history.empty:
            errors.append(NO_HISTORY_ERROR)
        return history

    def _load_info(self, ticker: str, warnings: list[str]) -> dict[str, object]:
        try:
            return self._info_loader(ticker)
        except Exception as exc:
            logger.warning(
                "yfinance metadata load failed",
                extra={
                    "event": "stock_analysis.metadata.failed",
                    "ticker": ticker,
                    "provider": "yfinance",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            warnings.append(METADATA_LOAD_WARNING)
            return {}


def normalize_stock_id_to_yfinance(stock: StockRecord) -> str:
    symbol = stock.symbol.strip().upper()
    if stock.market == "US":
        return symbol
    if stock.market == "TW":
        suffix = ".TWO" if stock.exchange == "TPEx" else ".TW"
        return f"{stock.symbol.strip()}{suffix}"
    return symbol


def merge_news_citations(
    provider_citations: tuple[AnalysisCitation, ...],
    news_context: AnalysisNewsContext | None,
) -> tuple[AnalysisCitation, ...]:
    """Prefer backend-selected news URLs, then dedupe provider citations by URL."""
    merged: list[AnalysisCitation] = []
    seen: set[str] = set()

    if news_context is not None and news_context.available:
        for item in news_context.items:
            url = item.url.strip()
            key = _citation_dedupe_key(url)
            if not url or key in seen:
                continue
            seen.add(key)
            merged.append(
                AnalysisCitation(
                    url=url,
                    title=item.title,
                    source=item.source,
                    published_at=item.published_at,
                )
            )

    for citation in provider_citations:
        url = citation.url.strip()
        key = _citation_dedupe_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        merged.append(citation)
    return tuple(merged)


def _citation_dedupe_key(url: str) -> str:
    """Ignore scheme, ``www.``, trailing slash, and case when comparing URLs."""
    cleaned = _URL_SCHEME_RE.sub("", url.strip().lower())
    cleaned = _URL_HOST_PREFIX_RE.sub("", cleaned)
    return cleaned.rstrip("/")


def _history_from_chart_provider(stock_id: str) -> pd.DataFrame | None:
    """Load the 1Y daily series ``get_history_chart`` uses, including SMA warm-up."""
    try:
        data = quote_service.get_history(
            stock_id,
            "1Y",
            limit=history_fetch_limit("1Y"),
        )
    except Exception as exc:  # noqa: BLE001 — fall back to yfinance
        logger.warning(
            "Chart history load for analysis failed",
            extra={
                "event": "stock_analysis.chart_history.failed",
                "stock_id": stock_id,
                "error_type": type(exc).__name__,
            },
        )
        return None
    if data is None:
        return None
    _stock, candles = data
    if not candles:
        return None
    return _candles_to_history_frame(candles)


def _candles_to_history_frame(candles: list[CandleRecord]) -> pd.DataFrame:
    """Turn chart candles into the OHLC frame the metric helpers already consume."""
    index = pd.DatetimeIndex([pd.Timestamp(candle.time) for candle in candles])
    return pd.DataFrame(
        {
            "Open": [float(candle.open) for candle in candles],
            "High": [float(candle.high) for candle in candles],
            "Low": [float(candle.low) for candle in candles],
            "Close": [float(candle.close) for candle in candles],
            "Volume": [int(candle.volume) for candle in candles],
        },
        index=index,
    )


def _load_history_from_yfinance(ticker: str) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    return yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False)


def _load_info_from_yfinance(ticker: str) -> dict[str, object]:
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    ticker_client = yf.Ticker(ticker)
    info = dict(ticker_client.info or {})
    fast_info = getattr(ticker_client, "fast_info", None)
    if fast_info is not None:
        for key, target_key in (
            ("currency", "currency"),
            ("market_cap", "marketCap"),
            ("lastPrice", "currentPrice"),
        ):
            try:
                value = fast_info.get(key)
            except Exception:
                value = None
            if value is not None and target_key not in info:
                info[target_key] = value
    return info


def _has_usable_price_history(history: pd.DataFrame) -> bool:
    return not select_price_series(history).empty


def _has_meaningful_yfinance_info(info: dict[str, object]) -> bool:
    return any(
        _has_meaningful_value(info.get(key))
        for key in (
            "longName",
            "shortName",
            "currency",
            "sector",
            "industry",
            "marketCap",
            "trailingPE",
            "forwardPE",
            "currentPrice",
            "regularMarketPrice",
            "previousClose",
        )
    )


def _has_meaningful_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return True


def _build_computed_data(
    stock: StockRecord,
    ticker: str,
    history: pd.DataFrame,
    info: dict[str, object],
    *,
    news_context: AnalysisNewsContext | None = None,
) -> ComputedStockAnalysisData:
    # All metrics below share one latest session so the nested technical block
    # and the top-level legacy fields cannot describe different candles.
    history = _completed_sessions_or_raw(stock, history)

    price_series = select_price_series(history)
    close_series = clean_numeric_series(history["Close"]) if "Close" in history else pd.Series(dtype="float64")
    volume_series = clean_numeric_series(history["Volume"]) if "Volume" in history else pd.Series(dtype="float64")

    current_price = _coerce_float(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose"),
    )
    if current_price is None:
        fallback_series = close_series if not close_series.empty else price_series
        if not fallback_series.empty:
            current_price = round(float(fallback_series.iloc[-1]), 2)

    sma_50 = calculate_simple_moving_average(price_series, 50)
    sma_200 = calculate_simple_moving_average(price_series, 200)
    return_1m = calculate_return_percent(price_series, RETURN_WINDOWS["return1mPercent"])
    return_3m = calculate_return_percent(price_series, RETURN_WINDOWS["return3mPercent"])
    return_6m = calculate_return_percent(price_series, RETURN_WINDOWS["return6mPercent"])
    return_1y = calculate_return_percent(price_series, RETURN_WINDOWS["return1yPercent"])

    technical = _safe_technical_analysis(stock, history)
    institutional = _safe_institutional_analysis(stock)
    fundamental = _safe_fundamental_outlook(stock, info)
    news_payload = _news_prompt_payload(news_context)

    return ComputedStockAnalysisData(
        ticker=ticker,
        company_name=_coerce_str(info.get("longName"))
        or _coerce_str(info.get("shortName"))
        or stock.name,
        currency=_coerce_str(info.get("currency")) or stock.currency,
        sector=_coerce_str(info.get("sector")),
        industry=_coerce_str(info.get("industry")),
        market_cap=_coerce_int(info.get("marketCap")),
        trailing_pe=_coerce_float(info.get("trailingPE")),
        forward_pe=_coerce_float(info.get("forwardPE")),
        current_price=current_price,
        return_1m_percent=return_1m,
        return_3m_percent=return_3m,
        return_6m_percent=return_6m,
        return_1y_percent=return_1y,
        sma_50=sma_50,
        sma_200=sma_200,
        price_vs_sma_50_percent=calculate_price_vs_average_percent(current_price, sma_50),
        price_vs_sma_200_percent=calculate_price_vs_average_percent(current_price, sma_200),
        rsi_14=calculate_rsi(price_series, period=14),
        volatility_30d_annualized=calculate_annualized_volatility(price_series, window=30),
        max_drawdown_1y_percent=calculate_max_drawdown(price_series),
        average_volume_30d=calculate_average_volume(volume_series, window=30),
        technical_analysis=technical,
        institutional_analysis=institutional,
        fundamental_outlook=fundamental,
        recent_news_context=news_payload,
    )


def _log_build_failure(event: str, stock: StockRecord, exc: Exception) -> None:
    logger.warning(
        "Stock analysis context build failed",
        extra={
            "event": event,
            "stock_id": stock.id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )


def _completed_sessions_or_raw(stock: StockRecord, history: pd.DataFrame) -> pd.DataFrame:
    try:
        return completed_session_history(history, market=stock.market)
    except Exception as exc:  # noqa: BLE001 — fall back to the raw frame
        _log_build_failure("stock_analysis.completed_sessions.failed", stock, exc)
        return history


def _safe_technical_analysis(stock: StockRecord, history: pd.DataFrame) -> dict[str, object]:
    try:
        return build_technical_analysis(history, market=stock.market)
    except Exception as exc:  # noqa: BLE001 — never block the rest of analysis
        _log_build_failure("stock_analysis.technical.failed", stock, exc)
        return {
            "period": "1Y",
            "availability": "unavailable",
            "failureReason": f"build_failed:{type(exc).__name__}",
        }


def _safe_institutional_analysis(stock: StockRecord) -> dict[str, object] | None:
    try:
        return build_institutional_analysis(stock.id, stock)
    except Exception as exc:  # noqa: BLE001
        _log_build_failure("stock_analysis.institutional.failed", stock, exc)
        if stock.market != "TW":
            return None
        return {
            "available": False,
            "reason": "build_failed",
            "reasonDetail": type(exc).__name__,
            "market": "TW",
        }


def _safe_fundamental_outlook(
    stock: StockRecord,
    info: dict[str, object],
) -> dict[str, object]:
    try:
        return build_fundamental_outlook(stock, info)
    except Exception as exc:  # noqa: BLE001
        _log_build_failure("stock_analysis.fundamental.failed", stock, exc)
        return {
            "available": False,
            "availability": "unavailable",
            "confidence": "low",
            "missingInputs": ["fundamentalOutlook"],
            "failureReason": f"build_failed:{type(exc).__name__}",
        }


def _news_prompt_payload(
    news_context: AnalysisNewsContext | None,
) -> dict[str, object] | None:
    if news_context is None:
        return None
    payload = news_context.to_prompt_payload()["recentNewsContext"]
    return dict(payload) if isinstance(payload, dict) else None


def apply_cached_news_context(
    computed: ComputedStockAnalysisData,
    news_context: dict[str, object] | None,
) -> ComputedStockAnalysisData:
    """Replace live news with the snapshot tied to a cached AI narrative."""
    snapshot = dict(news_context) if news_context is not None else None
    return cast(
        ComputedStockAnalysisData,
        replace(computed, recent_news_context=snapshot),
    )


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


stock_analysis_service = StockAnalysisService()
