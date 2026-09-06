"""Create time domain baseline tables.

Revision ID: 0001_time_baseline
Revises:
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_time_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employee_read",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("manager_id", sa.Uuid(), nullable=True),
        sa.Column("pto_entitlement_days", sa.Integer(), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('ACTIVE','INACTIVE')", name="ck_employee_read_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_employee_read_manager_id", "employee_read", ["manager_id"])
    op.create_table(
        "timesheets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("overtime_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED')",
            name="ck_timesheets_status",
        ),
        sa.CheckConstraint("period_end >= period_start", name="ck_timesheets_period"),
        sa.CheckConstraint("total_hours >= 0", name="ck_timesheets_total_hours"),
        sa.CheckConstraint("overtime_hours >= 0", name="ck_timesheets_overtime_hours"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "employee_id", "period_start", name="uq_timesheets_employee_period"
        ),
    )
    op.create_index("ix_timesheets_employee_id", "timesheets", ["employee_id"])
    op.create_index(
        "ix_timesheets_employee_period",
        "timesheets",
        ["employee_id", "period_start", "period_end"],
    )
    op.create_table(
        "time_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("timesheet_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("project_code", sa.String(length=50), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.CheckConstraint("hours > 0 AND hours <= 24", name="ck_time_entries_hours"),
        sa.ForeignKeyConstraint(["timesheet_id"], ["timesheets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "timesheet_id", "work_date", name="uq_time_entries_timesheet_work_date"
        ),
    )
    op.create_index("ix_time_entries_timesheet_id", "time_entries", ["timesheet_id"])


def downgrade() -> None:
    op.drop_index("ix_time_entries_timesheet_id", table_name="time_entries")
    op.drop_table("time_entries")
    op.drop_index("ix_timesheets_employee_period", table_name="timesheets")
    op.drop_index("ix_timesheets_employee_id", table_name="timesheets")
    op.drop_table("timesheets")
    op.drop_index("ix_employee_read_manager_id", table_name="employee_read")
    op.drop_table("employee_read")
