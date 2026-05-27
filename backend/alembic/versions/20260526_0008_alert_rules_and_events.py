"""add alert rules and alert events

Revision ID: 20260526_0008
Revises: 20260516_0007
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260526_0008"
down_revision = "20260516_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "last_condition_met",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("last_observed_price", sa.Float(), nullable=True),
        sa.Column("last_observed_quote_timestamp", sa.String(length=64), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_rules_enabled_stock",
        "alert_rules",
        ["enabled", "stock_id"],
        unique=False,
    )
    op.create_index(
        "ix_alert_rules_user_enabled",
        "alert_rules",
        ["user_id", "enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_rules_user_id"),
        "alert_rules",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_rule_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.String(length=32), nullable=False),
        sa.Column("rule_type", sa.String(length=32), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("observed_price", sa.Float(), nullable=False),
        sa.Column("quote_timestamp", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["alert_rule_id"], ["alert_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_events_user_triggered",
        "alert_events",
        ["user_id", "triggered_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_events_alert_rule_id"),
        "alert_events",
        ["alert_rule_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_alert_events_user_id"),
        "alert_events",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_alert_events_user_id"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_alert_rule_id"), table_name="alert_events")
    op.drop_index("ix_alert_events_user_triggered", table_name="alert_events")
    op.drop_table("alert_events")

    op.drop_index(op.f("ix_alert_rules_user_id"), table_name="alert_rules")
    op.drop_index("ix_alert_rules_user_enabled", table_name="alert_rules")
    op.drop_index("ix_alert_rules_enabled_stock", table_name="alert_rules")
    op.drop_table("alert_rules")
