# Atlas Time Service

Timesheets, time entries, and PTO balance projection for Atlas HRMS.

```bash
uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run pytest
uv run uvicorn app.main:app --reload --port 8002
```
