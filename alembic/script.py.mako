"""Alembic migration environment."""

# alembic.ini reads script_location = alembic, so this file is at alembic/script.py.mako

revision: str
down_revision: str | None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
