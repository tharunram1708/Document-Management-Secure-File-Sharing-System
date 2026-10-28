"""Add RBAC roles and document sharing."""
from alembic import op
import sqlalchemy as sa

revision = "0003_rbac_shares"
down_revision = "0002_create_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET role = 'user' WHERE role = 'member'")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("role", existing_type=sa.String(length=30), server_default="user")
    op.create_table(
        "document_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False, server_default="read"),
        sa.UniqueConstraint("document_id", "user_id", name="uq_document_share_user"),
    )
    op.create_index("ix_document_shares_document_id", "document_shares", ["document_id"])
    op.create_index("ix_document_shares_user_id", "document_shares", ["user_id"])


def downgrade():
    op.drop_index("ix_document_shares_user_id", table_name="document_shares")
    op.drop_index("ix_document_shares_document_id", table_name="document_shares")
    op.drop_table("document_shares")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("role", existing_type=sa.String(length=30), server_default="member")
