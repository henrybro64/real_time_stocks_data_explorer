from __future__ import annotations

import pytest

from app.services.stock_analysis_language import (
    DEFAULT_ANALYSIS_LANGUAGE,
    SUPPORTED_ANALYSIS_LANGUAGES,
    normalize_analysis_language,
)


def test_supported_languages_and_default_match_app() -> None:
    assert SUPPORTED_ANALYSIS_LANGUAGES == frozenset({"en", "zh-TW"})
    assert DEFAULT_ANALYSIS_LANGUAGE == "zh-TW"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "zh-TW"),
        ("", "zh-TW"),
        ("   ", "zh-TW"),
        ("en", "en"),
        ("EN", "en"),
        (" zh-TW ", "zh-TW"),
        ("zh_TW", "zh-TW"),
        ("zh-tw", "zh-TW"),
        ("ZH_tw", "zh-TW"),
    ],
)
def test_normalize_analysis_language_accepts_aliases(value: str | None, expected: str) -> None:
    assert normalize_analysis_language(value) == expected


@pytest.mark.parametrize("value", ["fr", "zh", "zh-CN", "english", "zhTW"])
def test_normalize_analysis_language_rejects_unsupported(value: str) -> None:
    with pytest.raises(ValueError, match="Unsupported analysis language"):
        normalize_analysis_language(value)
