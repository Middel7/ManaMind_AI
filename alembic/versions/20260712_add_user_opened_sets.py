"""add user_opened_sets table

Revision ID: 20260712_add_user_opened_sets
Revises: 20260711_add_auth_tables
Create Date: 2026-07-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_add_user_opened_sets"
down_revision: Union[str, None] = "20260711_add_auth_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_opened_sets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("set_code", sa.Text(), nullable=False),
        sa.UniqueConstraint("user_id", "set_code", name="uq_user_opened_sets"),
    )
    op.create_index("ix_user_opened_sets_user_id", "user_opened_sets", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_opened_sets_user_id", table_name="user_opened_sets")
    op.drop_table("user_opened_sets")
