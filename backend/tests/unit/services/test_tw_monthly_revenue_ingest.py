from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    TwFundamentalRawFetch,
    TwMonthlyRevenueObservation,
    TwMonthlyRevenueRevision,
)
from app.services import http_client as http_client_module
from app.services.http_client import HttpResponseMeta
from app.services.tw_insights_service import (
    TW_INSIGHTS_DIVIDENDS_ARE_SYNTHETIC,
    TW_INSIGHTS_REVENUE_IS_SYNTHETIC,
    get_tw_stock_insights,
)
from app.services.tw_monthly_revenue.service import (
    collect_latest_monthly_revenue,
    get_monthly_revenue_for_stock,
    ingest_board_monthly_revenue,
    observation_period_is_stale,
)
from app.services.tw_research.first_pass import _attach_monthly_revenue_field
from app.services.tw_research.enrichment import EnrichmentResult
from tests.support import stock_record

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tw_monthly_revenue"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(_BACKEND_ROOT))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture()
def session_factory(tmp_path, monkeypatch):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'monthly_revenue.db'}"
    monkeypatch.setattr("app.core.config.settings.database_url", database_url)
    command.upgrade(_alembic_config(database_url), "head")
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.db.session.SessionLocal", factory)
    return factory


def _body(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _fetch_factory(bodies: dict[str, HttpResponseMeta]):
    def _fetch(url: str, **kwargs: object) -> HttpResponseMeta:
        for key, meta in bodies.items():
            if key in url:
                return meta
        raise AssertionError(f"unexpected url {url}")

    return _fetch


def test_ingest_both_boards_idempotent_and_revision(session_factory) -> None:
    twse_body = _body("twse_latest_month.json")
    tpex_body = _body("tpex_latest_month.json")
    fetch = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"twse-1"', "last-modified": "Thu, 26 Nov 2026 21:24:42 GMT"},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            ),
            "mopsfin_t187ap05_O": HttpResponseMeta(
                status_code=200,
                headers={"etag": '"tpex-1"', "last-modified": "Fri, 27 Nov 2026 10:11:36 GMT"},
                content=tpex_body,
                url="https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            ),
        }
    )

    with session_factory() as session:
        results = collect_latest_monthly_revenue(session, fetch_fn=fetch)
        assert [item.outcome for item in results] == ["accepted", "accepted"]
        first_count = session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one()
        assert first_count >= 820 + 620

        first_twse = session.execute(
            select(TwFundamentalRawFetch)
            .where(
                TwFundamentalRawFetch.board == "TWSE",
                TwFundamentalRawFetch.validation_status == "accepted",
            )
            .order_by(TwFundamentalRawFetch.id.asc())
        ).scalars().first()
        assert first_twse is not None
        assert first_twse.raw_body is not None

        # Identical repeat → body dedupe + unchanged observations, no revisions.
        results2 = collect_latest_monthly_revenue(session, fetch_fn=fetch)
        assert all(item.outcome == "accepted" for item in results2)
        assert all(item.stats.get("revised", 0) == 0 for item in results2)
        assert session.execute(select(func.count()).select_from(TwMonthlyRevenueRevision)).scalar_one() == 0
        second_twse = session.execute(
            select(TwFundamentalRawFetch)
            .where(
                TwFundamentalRawFetch.board == "TWSE",
                TwFundamentalRawFetch.validation_status == "accepted",
            )
            .order_by(TwFundamentalRawFetch.id.desc())
        ).scalars().first()
        assert second_twse is not None
        assert second_twse.id != first_twse.id
        assert second_twse.raw_body is None
        assert second_twse.body_ref_fetch_id == first_twse.id
        assert (
            session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one()
            == first_count
        )

        # Changed value for 2330 → revision preserved.
        payload = json.loads(twse_body.decode("utf-8"))
        for row in payload:
            if row["公司代號"] == "2330":
                row["營業收入-當月營收"] = "999999999"
                break
        changed = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        fetch_changed = _fetch_factory(
            {
                "t187ap05_L": HttpResponseMeta(
                    status_code=200,
                    headers={"etag": 'W/"twse-2"'},
                    content=changed,
                    url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                ),
                "mopsfin_t187ap05_O": HttpResponseMeta(
                    status_code=200,
                    headers={"etag": '"tpex-1"'},
                    content=tpex_body,
                    url="https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
                ),
            }
        )
        twse_result = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_changed)
        assert twse_result.outcome == "accepted"
        assert twse_result.stats.get("revised", 0) >= 1
        revisions = session.execute(select(TwMonthlyRevenueRevision)).scalars().all()
        assert revisions
        obs = get_monthly_revenue_for_stock(session, "TW:2330")
        assert obs is not None
        assert obs["revenueNtThousand"] == 999999999.0


def test_identical_200_bodies_dedupe_raw_storage(session_factory) -> None:
    twse_body = _body("twse_latest_month.json")
    fetch = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"same"'},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            )
        }
    )
    with session_factory() as session:
        first = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch)
        second = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch)
        assert first.outcome == "accepted"
        assert second.outcome == "accepted"
        first_row = session.get(TwFundamentalRawFetch, first.raw_fetch_id)
        second_row = session.get(TwFundamentalRawFetch, second.raw_fetch_id)
        assert first_row is not None and second_row is not None
        assert first_row.raw_body is not None
        assert second_row.raw_body is None
        assert second_row.body_ref_fetch_id == first_row.id
        assert second_row.body_sha256 == first_row.body_sha256
        count = session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one()
        assert count == first.row_count == second.row_count


def test_rejected_incomplete_keeps_last_valid(session_factory) -> None:
    twse_body = _body("twse_latest_month.json")
    fetch_ok = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"ok"'},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            )
        }
    )
    with session_factory() as session:
        ok = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_ok)
        assert ok.outcome == "accepted"
        before = session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one()

        fetch_bad = _fetch_factory(
            {
                "t187ap05_L": HttpResponseMeta(
                    status_code=200,
                    headers={"etag": 'W/"bad"'},
                    content=_body("twse_incomplete.json"),
                    url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                )
            }
        )
        bad = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_bad)
        assert bad.outcome == "rejected"
        after = session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one()
        assert after == before
        rejected = session.execute(
            select(TwFundamentalRawFetch).where(TwFundamentalRawFetch.validation_status == "rejected")
        ).scalars().all()
        assert rejected
        still = get_monthly_revenue_for_stock(session, "TW:2330")
        assert still is not None


def test_conditional_304_without_usable_cache_rejected(session_factory) -> None:
    def fetch_304(url: str, **kwargs: object) -> HttpResponseMeta:
        headers = kwargs.get("headers") or {}
        assert "If-None-Match" not in headers  # no accepted cache ⇒ no conditional
        return HttpResponseMeta(
            status_code=304,
            headers={"etag": 'W/"x"'},
            content=b"",
            url=url,
        )

    with session_factory() as session:
        # Force a 304 even without sending If-None-Match (provider quirk).
        result = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_304)
        assert result.validation_status == "rejected_304_no_cache"
        assert len(result.validation_status) <= 32
        assert session.execute(select(func.count()).select_from(TwMonthlyRevenueObservation)).scalar_one() == 0


def test_conditional_sent_only_with_accepted_cache(session_factory) -> None:
    twse_body = _body("twse_latest_month.json")
    seen_headers: list[dict[str, str]] = []

    def fetch(url: str, **kwargs: object) -> HttpResponseMeta:
        headers = dict(kwargs.get("headers") or {})
        seen_headers.append(headers)
        if headers.get("If-None-Match"):
            return HttpResponseMeta(
                status_code=304,
                headers={"etag": headers["If-None-Match"]},
                content=b"",
                url=url,
            )
        return HttpResponseMeta(
            status_code=200,
            headers={"etag": 'W/"cached"'},
            content=twse_body,
            url=url,
        )

    with session_factory() as session:
        first = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch)
        second = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch)
        assert first.outcome == "accepted"
        assert second.outcome == "not_modified"
        assert "If-None-Match" not in seen_headers[0]
        assert seen_headers[1].get("If-None-Match") == 'W/"cached"'


def test_real_path_304_not_modified_via_mock_transport(session_factory, monkeypatch) -> None:
    twse_body = _body("twse_latest_month.json")
    state = {"phase": "first"}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["phase"] == "first":
            return httpx.Response(
                200,
                headers={"etag": 'W/"live-cache"', "content-type": "application/json"},
                content=twse_body,
                request=request,
            )
        assert request.headers.get("if-none-match") == 'W/"live-cache"'
        return httpx.Response(
            304,
            headers={"etag": 'W/"live-cache"'},
            content=b"",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(http_client_module, "_shared_client", client)

    with session_factory() as session:
        first = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=None)
        assert first.outcome == "accepted"
        state["phase"] = "second"
        second = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=None)
        assert second.outcome == "not_modified"


def test_real_path_500_still_fetch_failed(session_factory, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"nope", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    monkeypatch.setattr(http_client_module, "_shared_client", client)

    with session_factory() as session:
        result = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=None)
        assert result.outcome == "fetch_failed"
        assert result.validation_status == "fetch_failed"


def test_insights_and_first_pass_use_stored_official_data(session_factory, monkeypatch) -> None:
    assert TW_INSIGHTS_REVENUE_IS_SYNTHETIC is False
    assert TW_INSIGHTS_DIVIDENDS_ARE_SYNTHETIC is False

    twse_body = _body("twse_latest_month.json")
    fetch = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"1"'},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            )
        }
    )
    with session_factory() as session:
        ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch)

    monkeypatch.setattr(
        "app.services.tw_insights_service._fetch_yahoo_compare",
        lambda stock: None,
    )
    stock = stock_record(
        "TW:2330",
        market="TW",
        symbol="2330",
        name="TSMC",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    insights = get_tw_stock_insights(stock)
    assert insights["revenue"]["status"] == "ok"
    assert insights["revenue"]["latestRevenueK"] == 586890319
    assert insights["revenue"]["stale"] is False
    assert len(insights["revenue"]["points"]) == 1  # no fabricated multi-month trend
    assert insights["dividends"]["status"] == "unavailable"
    assert insights["dividends"]["years"] == []

    missing = stock_record(
        "TW:9999",
        market="TW",
        symbol="9999",
        name="Missing Co",
        exchange="TWSE",
        currency="TWD",
        timezone="Asia/Taipei",
    )
    missing_insights = get_tw_stock_insights(missing)
    assert missing_insights["revenue"]["status"] == "unavailable"

    scratch = EnrichmentResult(stock_id="TW:2330", qualified=True)
    _attach_monthly_revenue_field(scratch, stock_id="TW:2330", captured_at="2026-11-27T00:00:00Z")
    field = scratch.fields["monthly_revenue_yoy_mom"]
    assert field.status == "ok"
    assert field.source_as_of == "2026-10"
    assert field.value["momPercent"] is not None


def test_attach_monthly_revenue_reraises_infrastructure_failures(monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from app.services.tw_research.enrichment import EnrichmentResult
    from app.services.tw_research.first_pass import _attach_monthly_revenue_field

    class _BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def boom_get(_session, _stock_id):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _BrokenSession())
    monkeypatch.setattr(
        "app.services.tw_monthly_revenue.service.get_monthly_revenue_for_stock",
        boom_get,
    )
    scratch = EnrichmentResult(stock_id="TW:2330", qualified=True)
    with pytest.raises(OperationalError):
        _attach_monthly_revenue_field(
            scratch,
            stock_id="TW:2330",
            captured_at="2026-11-27T00:00:00Z",
        )
    assert "monthly_revenue_yoy_mom" not in scratch.fields


def test_observation_stale_flag_uses_taipei_calendar_months() -> None:
    now = datetime(2026, 11, 27, tzinfo=ZoneInfo("Asia/Taipei"))
    assert observation_period_is_stale("202610", now=now) is False  # 1 month behind
    assert observation_period_is_stale("202609", now=now) is False  # 2 months behind
    assert observation_period_is_stale("202608", now=now) is True  # 3 months behind


def test_unchanged_financials_still_refresh_report_generated_date(session_factory) -> None:
    twse_body = _body("twse_latest_month.json")
    fetch_first = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"meta-1"'},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            )
        }
    )
    payload = json.loads(twse_body.decode("utf-8"))
    for row in payload:
        if row["公司代號"] == "2330":
            row["出表日期"] = "1151201"  # 2026-12-01 — financials unchanged
            row["公司名稱"] = "TSMC-RENAMED"
            break
    fetch_meta = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"meta-2"'},
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            )
        }
    )
    with session_factory() as session:
        first = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_first)
        assert first.outcome == "accepted"
        second = ingest_board_monthly_revenue(session, board="TWSE", fetch_fn=fetch_meta)
        assert second.outcome == "accepted"
        assert second.stats.get("revised", 0) == 0
        assert second.stats.get("unchanged", 0) >= 1
        obs = get_monthly_revenue_for_stock(session, "TW:2330")
        assert obs is not None
        assert obs["reportGeneratedDate"] == "2026-12-01"
        row = session.execute(
            select(TwMonthlyRevenueObservation).where(
                TwMonthlyRevenueObservation.stock_id == "TW:2330"
            )
        ).scalars().first()
        assert row is not None
        assert row.company_name == "TSMC-RENAMED"
        assert row.revision_count == 0


def test_advisory_lock_skips_second_board_without_raw_fetch(session_factory, monkeypatch) -> None:
    twse_body = _body("twse_latest_month.json")
    tpex_body = _body("tpex_latest_month.json")
    fetch = _fetch_factory(
        {
            "t187ap05_L": HttpResponseMeta(
                status_code=200,
                headers={"etag": 'W/"twse-lock"'},
                content=twse_body,
                url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
            ),
            "mopsfin_t187ap05_O": HttpResponseMeta(
                status_code=200,
                headers={"etag": '"tpex-lock"'},
                content=tpex_body,
                url="https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
            ),
        }
    )
    lock_calls: list[str] = []

    def fake_lock(_session, *, board: str) -> bool:
        lock_calls.append(board)
        return board != "TPEx"

    monkeypatch.setattr(
        "app.services.tw_monthly_revenue.service._try_collect_advisory_lock",
        fake_lock,
    )

    with session_factory() as session:
        results = collect_latest_monthly_revenue(session, fetch_fn=fetch)
        assert [item.outcome for item in results] == ["accepted", "skipped_locked"]
        assert lock_calls == ["TWSE", "TPEx"]
        tpex_raw = session.execute(
            select(TwFundamentalRawFetch).where(TwFundamentalRawFetch.board == "TPEx")
        ).scalars().all()
        assert tpex_raw == []
        twse_raw = session.execute(
            select(TwFundamentalRawFetch).where(TwFundamentalRawFetch.board == "TWSE")
        ).scalars().all()
        assert twse_raw
        assert all(row.validation_status == "accepted" for row in twse_raw)
