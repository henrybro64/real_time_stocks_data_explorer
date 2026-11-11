"""Add optimistic concurrency revision to research runs (#341)

Revision ID: 20261112_0026
Revises: 20261112_0025
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261112_0026"
down_revision = "20261112_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tw_research_research_runs",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tw_research_research_runs", "revision")
