"""add user AI provider preference

Revision ID: 20260516_0007
Revises: 20260505_0006
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260516_0007"
down_revision = "20260505_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ai_preferences",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("preferred_provider", sa.String(length=32), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_ai_preferences")
