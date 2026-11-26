import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_states",
        sa.Column("id", sa.String(63), primary_key=True),
        sa.Column("lock_token", sa.String(36)),
        sa.Column("lock_until", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(63), sa.ForeignKey("source_states.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("records_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(300)),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.CheckConstraint("status IN ('running','succeeded','failed')", name="ck_sync_status"),
    )
    op.create_index("ix_sync_runs_source_id", "sync_runs", ["source_id"])
    op.create_table(
        "raw_records",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("sync_runs.id"), primary_key=True),
        sa.Column("row_number", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "sales_facts",
        sa.Column("source_id", sa.String(63), sa.ForeignKey("source_states.id"), primary_key=True),
        sa.Column("external_id", sa.String(200), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("sync_runs.id"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(100), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("customer_id", sa.String(200)),
        sa.CheckConstraint("amount >= 0", name="ck_sales_amount"),
    )
    op.create_index("ix_sales_facts_run_id", "sales_facts", ["run_id"])
    op.create_index("ix_sales_facts_occurred_at", "sales_facts", ["occurred_at"])
    op.execute("""CREATE VIEW mart_sales_documents AS
        SELECT source_id, external_id, entity_type, title, amount, currency, status,
               closed, occurred_at, updated_at
        FROM sales_facts""")
    op.execute("""CREATE VIEW mart_sales_summary AS
        SELECT source_id, entity_type, currency, status, COUNT(*) AS documents_count,
               SUM(amount) AS total_amount
        FROM sales_facts GROUP BY source_id, entity_type, currency, status""")
    if op.get_context().dialect.name == "postgresql":
        op.execute("""DO $$ BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_reader') THEN
            GRANT SELECT ON mart_sales_documents, mart_sales_summary TO bi_reader;
          END IF;
        END $$""")


def downgrade():
    op.execute("DROP VIEW mart_sales_summary")
    op.execute("DROP VIEW mart_sales_documents")
    op.drop_table("sales_facts")
    op.drop_table("raw_records")
    op.drop_table("sync_runs")
    op.drop_table("source_states")
