"""add auth tables: users, invitations, user_moxfield_decks, user_deck_cards + user_id on user_collection

Revision ID: 20260711_add_auth_tables
Revises: 20260705_add_user_collection
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260711_add_auth_tables"
down_revision: Union[str, None] = "20260705_add_user_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Table users ───────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),  # null si connexion Google uniquement
        sa.Column("google_id", sa.Text(), nullable=True, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="user"),  # 'admin' | 'user'
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_google_id", "users", ["google_id"])

    # ── Table invitations ─────────────────────────────────────────
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.Text(), nullable=False, unique=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("used_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_invitations_token", "invitations", ["token"])

    # ── Table user_moxfield_decks (remplace moxfield_decks.json) ─
    op.create_table(
        "user_moxfield_decks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deck_id", sa.Text(), nullable=False),
        sa.Column("moxfield_url", sa.Text(), nullable=True),
        sa.Column("commander", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locally_modified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "deck_id", name="uq_user_deck"),
    )
    op.create_index("ix_user_moxfield_decks_user_id", "user_moxfield_decks", ["user_id"])

    # ── Table user_deck_cards (remplace data/My decks/*.txt) ──────
    op.create_table(
        "user_deck_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commander", sa.Text(), nullable=False),
        sa.Column("card_name", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("user_id", "commander", "card_name", name="uq_user_deck_card"),
    )
    op.create_index("ix_user_deck_cards_user_commander", "user_deck_cards", ["user_id", "commander"])

    # ── Ajouter user_id sur user_collection ───────────────────────
    op.add_column("user_collection", sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_user_collection_user_id", "user_collection", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_collection_user_id", "user_collection")
    op.drop_column("user_collection", "user_id")
    op.drop_index("ix_user_deck_cards_user_commander", "user_deck_cards")
    op.drop_table("user_deck_cards")
    op.drop_index("ix_user_moxfield_decks_user_id", "user_moxfield_decks")
    op.drop_table("user_moxfield_decks")
    op.drop_index("ix_invitations_token", "invitations")
    op.drop_table("invitations")
    op.drop_index("ix_users_google_id", "users")
    op.drop_index("ix_users_email", "users")
    op.drop_table("users")
