"""fix user_collection user_id NOT NULL + FK cascade

Revision ID: 20260712_fix_user_collection_constraints
Revises: 20260712_merge_heads
Create Date: 2026-07-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260712_fix_ucoll"
down_revision: Union[str, None] = "20260712_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Supprimer les lignes orphelines avant d'ajouter NOT NULL
    op.execute("DELETE FROM user_collection WHERE user_id IS NULL")
    # Ajouter la contrainte NOT NULL
    op.alter_column("user_collection", "user_id", nullable=False)
    # Vérifier si la FK existe déjà, sinon l'ajouter avec ON DELETE CASCADE
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_type = 'FOREIGN KEY'
                  AND table_name = 'user_collection'
                  AND constraint_name = 'fk_user_collection_user_id'
            ) THEN
                ALTER TABLE user_collection
                ADD CONSTRAINT fk_user_collection_user_id
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE user_collection
        DROP CONSTRAINT IF EXISTS fk_user_collection_user_id
    """)
    op.alter_column("user_collection", "user_id", nullable=True)
