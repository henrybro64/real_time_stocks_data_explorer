"""widen volume and net-share columns to bigint

Revision ID: 20261011_0019
Revises: 20261011_0018
Create Date: 2026-10-11

Taiwan ranking volume is stored in shares (lots × 1000). Values above
2,147,483,647 overflow INTEGER (#275).
"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0019"
down_revision = "20261011_0018"
branch_labels = None
depends_on = None

_RANKING_ITEM_COLUMNS = (
    "volume",
    "volume_delta",
    "net_buy",
    "net_sell",
)

_TAIWAN_NET_SHARE_COLUMNS = (
    "foreign_net_shares",
    "investment_trust_net_shares",
    "dealer_self_net_shares",
    "dealer_hedge_net_shares",
    "dealer_net_shares",
    "total_net_shares",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite INTEGER already stores 64-bit values; recreate for type metadata.
        with op.batch_alter_table("ranking_snapshot_items", recreate="always") as batch_op:
            for column in _RANKING_ITEM_COLUMNS:
                batch_op.alter_column(
                    column,
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=True,
                )
        with op.batch_alter_table(
            "taiwan_institutional_daily",
            recreate="always",
        ) as batch_op:
            for column in _TAIWAN_NET_SHARE_COLUMNS:
                nullable = column in {
                    "dealer_self_net_shares",
                    "dealer_hedge_net_shares",
                }
                batch_op.alter_column(
                    column,
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=nullable,
                )
        return

    for column in _RANKING_ITEM_COLUMNS:
        op.alter_column(
            "ranking_snapshot_items",
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )
    for column in _TAIWAN_NET_SHARE_COLUMNS:
        nullable = column in {"dealer_self_net_shares", "dealer_hedge_net_shares"}
        op.alter_column(
            "taiwan_institutional_daily",
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "taiwan_institutional_daily",
            recreate="always",
        ) as batch_op:
            for column in reversed(_TAIWAN_NET_SHARE_COLUMNS):
                nullable = column in {
                    "dealer_self_net_shares",
                    "dealer_hedge_net_shares",
                }
                batch_op.alter_column(
                    column,
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=nullable,
                )
        with op.batch_alter_table("ranking_snapshot_items", recreate="always") as batch_op:
            for column in reversed(_RANKING_ITEM_COLUMNS):
                batch_op.alter_column(
                    column,
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=True,
                )
        return

    for column in reversed(_TAIWAN_NET_SHARE_COLUMNS):
        nullable = column in {"dealer_self_net_shares", "dealer_hedge_net_shares"}
        op.alter_column(
            "taiwan_institutional_daily",
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=nullable,
        )
    for column in reversed(_RANKING_ITEM_COLUMNS):
        op.alter_column(
            "ranking_snapshot_items",
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
