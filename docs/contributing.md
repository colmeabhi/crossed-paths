# Contributing

## Setup

Follow the [root README](../README.md). After `uv run pytest` shows **41 passed**, you're ready.

## Rules

1. **No Cypher outside `app/db.py`.** If a page needs data that no function provides, add a function to `db.py` (or ask the back-end owner to).
2. **The logged-in user is `st.session_state["username"]`.** Auth sets it on login; every page reads it. Don't use any other key.
3. **Keep return shapes stable.** Pages depend on the keys `db.py` returns. If you must change a shape, update [db-api.md](db-api.md) and tell the team.
4. **Never commit `.env`.** It holds passwords. Share connection details privately.
5. **Never write passwords to the database.** Store only the bcrypt hash via `create_user(..., password_hash, ...)`.
6. **No character images.** Initials avatars only.

## Adding a `db.py` function

```python
def get_something(username, limit=10):
    """One line saying what it returns → [user summary] / {...} / bool."""
    return [r["user"] for r in _read(
        f"""
        MATCH (u:User {{username: $u}})-[:SOMETHING]->(x:User)
        RETURN {USER_SUMMARY} AS user
        LIMIT $limit
        """,
        u=username, limit=limit)]
```

- Always pass values as **parameters** (`$u`), never with string formatting. This prevents injection.
- Reads use `_read(...)`, which returns a list of dicts. Writes use `_write(...)`, which returns counters such as `relationships_created`.
- Use `coalesce(x, default)` for properties that real sign-ups may not have (`house`, `sceneCount`, …).
- Return plain Python types, and reuse `USER_SUMMARY` for lists of people.
- Add a test to `tests/test_db.py` and a row to [db-api.md](db-api.md).

## Tests

```bash
uv run pytest                      # everything
uv run pytest tests/test_pipeline.py   # CSV checks only (no database needed)
uv run pytest tests/test_db.py -v      # database checks (Neo4j must be running and loaded)
```

- Database tests create users prefixed `zz_test_` and delete them afterwards. Use the same prefix for any test data you create.
- If Neo4j isn't reachable, database tests are **skipped**, not failed. Check the output says `passed`, not `skipped`.

## Working with the database

| Task | Command |
|---|---|
| Start / stop local Neo4j | `docker compose up -d` / `docker compose down` |
| Wipe local Neo4j completely | `docker compose down -v` |
| Rebuild character data (keeps sign-ups) | `uv run python pipeline/load_db.py --reset` |
| Explore visually | http://localhost:7474 |
| Switch to AuraDB | change `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` in `.env` |

AuraDB Free pauses after a few days without activity. Open it the day before a demo.

## Git

- Small commits that each do one thing, with a message saying what and why.
- Run `uv run pytest` before pushing.
- Pull before starting work: `git pull`.
