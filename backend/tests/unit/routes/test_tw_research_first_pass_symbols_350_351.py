"""First-pass symbols route: stockIds filter + owner isolation (#350/#351)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_symbols_stock_ids_filter_and_owner_isolation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list_symbols(job_id, *, user_id, **kwargs):  # noqa: ANN001
        captured["job_id"] = job_id
        captured["user_id"] = user_id
        captured["kwargs"] = kwargs
        stock_ids = kwargs.get("stock_ids") or []
        return {
            "jobId": job_id,
            "offset": kwargs.get("offset", 0),
            "limit": kwargs.get("limit", 50),
            "total": len(stock_ids),
            "statusFilter": kwargs.get("status"),
            "q": kwargs.get("q"),
            "completenessFilter": kwargs.get("completeness"),
            "stockIdsFilter": stock_ids or None,
            "records": [
                {
                    "stockId": sid,
                    "status": "failed",
                    "evidence": {},
                }
                for sid in stock_ids
            ],
        }

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.list_symbols",
        fake_list_symbols,
    )
    # Default TestClient user id is "default" via get_effective_user_id.
    response = client.get(
        "/api/tw-research/first-pass/jobs/fpj_own/symbols",
        params={
            "status": "failed",
            "stockIds": "TW:2330,TW:2330,TW:2303,,TW:2454",
            "limit": 50,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["result"]
    assert body["stockIdsFilter"] == ["TW:2330", "TW:2303", "TW:2454"]
    assert [row["stockId"] for row in body["records"]] == [
        "TW:2330",
        "TW:2303",
        "TW:2454",
    ]
    assert captured["kwargs"]["stock_ids"] == ["TW:2330", "TW:2303", "TW:2454"]
    assert captured["kwargs"]["status"] == "failed"

    # Cap at 50 ids.
    many = ",".join(f"TW:{1000 + i}" for i in range(60))
    capped = client.get(
        "/api/tw-research/first-pass/jobs/fpj_own/symbols",
        params={"stockIds": many},
    )
    assert capped.status_code == 200
    assert len(capped.json()["result"]["stockIdsFilter"]) == 50

    # Owner isolation: other user's job → 404.
    def fake_other(job_id, *, user_id, **kwargs):  # noqa: ANN001
        del kwargs
        raise KeyError(f"first_pass_job_not_found:{job_id}:{user_id}")

    monkeypatch.setattr(
        "app.api.routes.tw_research.first_pass_job_service.list_symbols",
        fake_other,
    )
    missing = client.get("/api/tw-research/first-pass/jobs/fpj_other/symbols")
    assert missing.status_code == 404
