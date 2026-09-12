"""Normalize AI stock-analysis language to the app's supported locales."""

from __future__ import annotations

SUPPORTED_ANALYSIS_LANGUAGES = frozenset({"en", "zh-TW"})
DEFAULT_ANALYSIS_LANGUAGE = "zh-TW"

_LANGUAGE_ALIASES = {
    "en": "en",
    "zh-tw": "zh-TW",
    "zh_tw": "zh-TW",
}


def normalize_analysis_language(value: str | None) -> str:
    """Return a supported analysis language code.

    Accepts common casing/separator variants such as ``EN``, ``zh_TW``, and
    ``zh-tw``. ``None`` or blank values fall back to
    :data:`DEFAULT_ANALYSIS_LANGUAGE`. Unsupported values raise ``ValueError``.
    """
    if value is None:
        return DEFAULT_ANALYSIS_LANGUAGE

    normalized = value.strip()
    if not normalized:
        return DEFAULT_ANALYSIS_LANGUAGE

    canonical = _LANGUAGE_ALIASES.get(normalized.casefold().replace(" ", ""))
    if canonical is None:
        supported = ", ".join(sorted(SUPPORTED_ANALYSIS_LANGUAGES))
        raise ValueError(
            f"Unsupported analysis language {value!r}; expected one of: {supported}"
        )
    return canonical
