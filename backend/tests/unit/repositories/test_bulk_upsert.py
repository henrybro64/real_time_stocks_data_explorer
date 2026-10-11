"""Daily market rows are written in chunks, not one statement per symbol (#274)."""

from __future__ import annotations

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, TaiwanInstitutionalDaily
from app.repositories.session import upsert_market_symbol_date_rows
from app.services.tw_institutional_service import TaiwanInstitutionalInvestorRecord


def test_market_symbol_date_upsert_is_one_insert_per_chunk() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[TaiwanInstitutionalDaily.__table__])
    factory = sessionmaker(bind=engine)
    inserts: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith("INSERT"):
            inserts.append(statement)

    records = [
        TaiwanInstitutionalInvestorRecord(
            date="2026-07-10",
            symbol=f"{index:04d}",
            name=f"N{index}",
            foreign_net_shares=1,
            investment_trust_net_shares=1,
            dealer_self_net_shares=1,
            dealer_hedge_net_shares=1,
            dealer_net_shares=1,
            total_net_shares=1,
        )
        for index in range(5)
    ]

    def build_row(record, trade_date):
        return TaiwanInstitutionalDaily(
            market="TWSE",
            symbol=record.symbol,
            date=trade_date,
            name=record.name,
            foreign_net_shares=record.foreign_net_shares,
            investment_trust_net_shares=record.investment_trust_net_shares,
            dealer_self_net_shares=record.dealer_self_net_shares,
            dealer_hedge_net_shares=record.dealer_hedge_net_shares,
            dealer_net_shares=record.dealer_net_shares,
            total_net_shares=record.total_net_shares,
            source="test",
        )

    stats = upsert_market_symbol_date_rows(
        factory,
        model=TaiwanInstitutionalDaily,
        market="TWSE",
        source="test",
        records=records,
        build_row=build_row,
        apply_update=lambda existing, record, source: None,
    )
    assert stats.inserted == 5
    assert stats.updated == 0
    assert len(inserts) == 1

    records[0] = TaiwanInstitutionalInvestorRecord(
        date="2026-07-10",
        symbol="0000",
        name="renamed",
        foreign_net_shares=9,
        investment_trust_net_shares=1,
        dealer_self_net_shares=1,
        dealer_hedge_net_shares=1,
        dealer_net_shares=1,
        total_net_shares=9,
    )
    inserts.clear()
    again = upsert_market_symbol_date_rows(
        factory,
        model=TaiwanInstitutionalDaily,
        market="TWSE",
        source="test",
        records=records,
        build_row=build_row,
        apply_update=lambda existing, record, source: None,
    )
    assert again.updated == 5
    assert again.inserted == 0
    assert len(inserts) == 1
    with factory() as session:
        renamed = session.execute(
            select(TaiwanInstitutionalDaily).where(TaiwanInstitutionalDaily.symbol == "0000")
        ).scalar_one()
    assert renamed.name == "renamed"
    assert renamed.total_net_shares == 9
