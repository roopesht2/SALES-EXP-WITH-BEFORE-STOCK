# Sales & Expense Tracker

Accounting tracker for Shree Balaji Associates, covering sales, purchases, expenses, payments, advances, bank accounts, ledgers, backups, and reports.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm --filter @workspace/sales-expense-tracker run dev` — run the browser app
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `SESSION_SECRET` — session signing secret for the Flask tracker

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `artifacts/sales-expense-tracker/index.html` — supplied tracker frontend
- `artifacts/api-server/app.py` — SQLite-backed Flask application and API
- `artifacts/api-server/src/` — Express health endpoint and proxy that runs the Flask app
- `artifacts/*/.replit-artifact/artifact.toml` — preview and production service definitions

## Architecture decisions

- The supplied HTML/JavaScript frontend is served statically by Vite to preserve its existing behavior.
- Express remains the managed `/api` service and proxies authenticated requests to the supplied Flask app.
- SQLite is retained because it is the storage model delivered with the supplied backend.
- Flask runs behind Gunicorn with one worker so SQLite writes stay serialized.

## Product

- Admin and operator login with date-window permissions
- Sales, purchases, expenses, advances, partial payments, and loading/unloading costs
- Client and vendor ledgers, receivables/payables, multi-bank account tracking, bank book
- JSON backup/restore, clear-all controls, PDF/Excel reporting, and Tally XML export

## User preferences

- Preserve the supplied tracker UI and accounting behavior unless the user asks for a change.

## Gotchas

- The initial database creates the supplied default users; change these credentials before public use.
- The web artifact depends on the `/api` service being published alongside it.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
