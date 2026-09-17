# Migrations

`migrations/versions/0001_baseline.py` captures the schema that is already
live in the `vcc_plastics` MySQL database (`database/schema.sql`,
`database/product_master_schema.sql` and `database/mold_master_schema.sql`,
copied verbatim into `migrations/versions/sql/` and executed via
`op.execute()`). It exists so Alembic has a baseline revision to build future
migrations on top of.

**Do NOT run `alembic upgrade head` against the existing dev database.** The
tables in `0001_baseline.py` already exist in `vcc_plastics` with real data;
re-running their `CREATE TABLE` / `INSERT ... ON DUPLICATE KEY UPDATE`
statements is unnecessary and risks colliding with the live schema.

Once you've reviewed the baseline migration, mark it as already applied
without executing it:

```powershell
alembic stamp head
```

From then on, create new migrations the normal way (`alembic revision -m "..."`,
then `alembic upgrade head`) for any schema change that isn't already live.

Note: the `factory_node_types` / `factory_structure_nodes`, `equipment_*` and
`system_settings` tables used by `app.modules.master_data.factory_structure`,
`app.modules.master_data.machines` and
`app.modules.administration.system_settings` have no corresponding `.sql`
file in this repository (they were not part of `database/*.sql`), so they are
**not** included in `0001_baseline.py` either. This mirrors the original
repository layout exactly - it is not something this restructuring
introduced.
