"""add user api keys table

Revision ID: 20260323_0002
Revises: 20260323_0001
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260323_0002"
down_revision = "20260323_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_api_keys_user_provider"),
    )
    op.create_index(op.f("ix_user_api_keys_user_id"), "user_api_keys", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_api_keys_user_id"), table_name="user_api_keys")
    op.drop_table("user_api_keys")
