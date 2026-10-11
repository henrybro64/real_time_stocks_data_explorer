"""canonicalize mixed-case user stock ids

Revision ID: 20261011_0020
Revises: 20261011_0019
Create Date: 2026-10-11

``US:aapl`` and ``US:AAPL`` were stored as different rows (#275). Rewrite
watchlist and portfolio ids to the canonical form. When both spellings exist,
the watchlist keeps one membership and positions merge quantity and cost.
Downgrade cannot restore the original spelling.
"""

from alembic import op

from app.services.stock_ids import collapse_mixed_case_stock_ids

revision = "20261011_0020"
down_revision = "20261011_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    collapse_mixed_case_stock_ids(op.get_bind())


def downgrade() -> None:
    pass
