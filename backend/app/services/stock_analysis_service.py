from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import pandas as pd

from app.models.stock import StockRecord
from app.services.ai_providers import (
    AIProvider,
    AIProviderConfigurationError,
    AIProviderError,
    AnalysisCitation,
    AnalysisSearchResult,
    create_ai_provider,
)
from app.services.quote_service import quote_service
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

try:
    import yfinance as yf
except ImportError:  # pragma: no cover - exercised through runtime config paths.
    yf = None


HistoryLoader = Callable[[str], pd.DataFrame]
InfoLoader = Callable[[str], dict[str, object]]
ProviderFactory = Callable[[], AIProvider | None]

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

    def to_prompt_payload(self) -> dict[str, object]:
        return {
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


class StockAnalysisService:
    def __init__(
        self,
        history_loader: HistoryLoader | None = None,
        info_loader: InfoLoader | None = None,
        provider_factory: ProviderFactory = create_ai_provider,
    ) -> None:
        self._history_loader = history_loader or _load_history_from_yfinance
        self._info_loader = info_loader or _load_info_from_yfinance
        self._provider_factory = provider_factory

    def analyze_stock(
        self,
        stock_id: str,
        *,
        provider_override: AIProvider | None = None,
        run_ai: bool = True,
    ) -> StockAnalysisResult:
        stock = quote_service.get_stock(stock_id)
        if stock is None:
            raise ValueError("Stock not found")

        ticker = normalize_stock_id_to_yfinance(stock)
        warnings: list[str] = []
        errors: list[str] = []
        history = self._load_history(ticker, errors)
        history = _coerce_history_frame(history)
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
        computed_data = _build_computed_data(stock, ticker, history, info)

        ai_provider: str | None = None
        ai_model: str | None = None
        ai_analysis: str | None = None
        citations: tuple[AnalysisCitation, ...] = ()
        search_results: tuple[AnalysisSearchResult, ...] = ()

        if run_ai:
            provider_config_error: str | None = None
            if provider_override is not None:
                provider = provider_override
            else:
                try:
                    provider = self._provider_factory()
                except AIProviderConfigurationError as exc:
                    provider_config_error = str(exc)
                    provider = None

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
                    prompt_payload["recentNewsContextAvailable"] = False
                    if warnings:
                        prompt_payload["warnings"] = list(warnings)
                    if errors:
                        prompt_payload["errors"] = list(errors)
                    try:
                        gen_result = provider.generate_stock_analysis(prompt_payload)
                        ai_analysis = gen_result.content
                        citations = gen_result.citations
                        search_results = gen_result.search_results
                        ai_provider = gen_result.provider
                        ai_model = gen_result.model
                    except (AIProviderError, Exception):
                        warnings.append(
                            "AI analysis is temporarily unavailable, but computed stock metrics are still available.",
                        )

        return StockAnalysisResult(
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
        )

    def _load_history(self, ticker: str, errors: list[str]) -> pd.DataFrame:
        try:
            history = self._history_loader(ticker)
        except Exception:
            errors.append(HISTORY_FETCH_ERROR)
            return _empty_price_history_frame()
        if history.empty:
            errors.append(NO_HISTORY_ERROR)
        return history

    def _load_info(self, ticker: str, warnings: list[str]) -> dict[str, object]:
        try:
            return self._info_loader(ticker)
        except Exception:
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
) -> ComputedStockAnalysisData:
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
