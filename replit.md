# NEFOR SPOT Telegram bot

Асинхронный Telegram-бот для калькуляции расходников и внутреннего складского учёта парикмахерской NEFOR SPOT.

## Run & Operate

- Workflow `NEFOR SPOT Telegram Bot` — единственный polling-процесс Telegram.
- `uv run python -m unittest discover -s tests -v` — автоматические тесты с временной базой.
- Required secret: `BOT_TOKEN` — Replit Secrets only.
- Optional env: `ADMIN_USER_ID`, `DATABASE_PATH` (default `data/nefor_spot.sqlite3`), SQLite `DATABASE_URL`, `APP_ENV`, `LOG_LEVEL`.
- Production: Reserved VM / VM Deployment with one always-running worker and persistent storage; do not use Autoscale for long polling.

## Stack

- Python 3.11, aiogram 3.x, aiosqlite, SQLite.
- Telegram handlers call the service layer; inventory mutations and finished calculations use transactions.
- Decimal values are stored as exact text and parsed into `Decimal`; money is rounded to cents with `ROUND_HALF_UP`.

## Where things live

- `salon_cost_bot/database.py` — schema, versioning, safe initial seeding, persistent async SQLite access.
- `salon_cost_bot/services.py` — calculation, pricing, inventory, history, backup and inventory-session operations.
- `salon_cost_bot/handlers/` — Telegram menus, master flow and admin flows.
- `salon_cost_bot/catalog.py` — initial categories, product/shade catalog, pricing rules and historical stock.
- `salon_cost_bot/backups.py` — consistent SQLite backup and CSV exports.
- `tests/` — isolated tests using temporary SQLite files.
- `README.md` — setup, data safety, hosting and restore guidance in Russian.

## Architecture decisions

- SQLite `user_version` migrations are forward-only; data is never dropped and a pre-migration backup is created when an existing file is upgraded.
- Physical products are separate from pricing rules; product-level rates override category/brand defaults.
- Calculation completion creates calculation snapshots and inventory movements in one transaction, keyed by a unique completion token.
- The process holds a non-blocking local lock and reports Telegram duplicate-polling conflicts. Use exactly one worker per bot token.
- SQLite is the current backend. `DATABASE_URL` accepts only a SQLite URL; PostgreSQL requires a future adapter.

## Product

- Masters can build multi-material cost calculations, use the two-phase acid-remover flow, and see only their own calculation history.
- Administrators can manage stock, receipts, write-offs, single/full inventory counts, pricing, products, global calculation history, movement history, and exports.

## User preferences

- All user-facing text is Russian and timestamps display in Europe/Moscow.
- Never expose `BOT_TOKEN`; never log it or store it in source or README.
- Keep master inventory and purchase-cost information private from non-admin users.
- Keep financial arithmetic in `Decimal`; do not change historical calculation snapshots when rates change.

## Gotchas

- Do not publish this long-polling bot as an Autoscale service; use an always-running VM and persistent database disk.
- Start only one bot process for each token. Local lock catches same-database duplicates; Telegram reports a second polling process on another host.
- `MemoryStorage` holds only unfinished conversational state. A restart cancels an unfinished draft without changing stock; saved data remains in SQLite.

## Pointers

- See `README.md` for run instructions, data safety, hosting and restore guidance.
