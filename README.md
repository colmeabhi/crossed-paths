# ENCORE — Crossed Paths

**ENCounter-based cOnnection REcommendation** · *Westeros Edition*

An ambient social network that turns real-world encounters (shared scenes, places, and name mentions) into connection suggestions. Built on a Neo4j property graph, with Game of Thrones characters as the demo users.

## Data pipeline

```bash
uv sync                                       # install deps (Python ≥ 3.10)
cp .env.example .env                          # then set NEO4J_PASSWORD
./scripts/fetch_data.sh                       # clone the raw dataset into data/raw/
uv run python pipeline/got_pipeline.py        # JSON → data/csv/*.csv
uv run pytest                                 # sanity checks on the CSVs
docker compose up -d                          # local Neo4j 5 (Browser: http://localhost:7474)
uv run python pipeline/load_db.py --reset     # CSVs → Neo4j (safe to rerun)
```

`--reset` removes only seeded data (character users and locations). Users who registered through the app are kept.

### Outputs (`data/csv/`, committed)

| File | Graph element |
|---|---|
| `users.csv` | `(:User)`: 157 characters with ≥ 10 scenes |
| `locations.csv` | `(:Location)`: 26 locations |
| `co_present.csv` | `CO_PRESENT {count, firstSeason}`: shared scenes, stored once |
| `was_at.csv` | `WAS_AT {count, firstSeason}`: user → location |
| `mentioned.csv` | `MENTIONED {count}`: speaker → character named in their lines |
| `family.csv` | `FAMILY {type}`: `CHILD_OF` / `SIBLING_OF` / `MARRIED_TO` |
| `follows_seed.csv` | `FOLLOWS {sinceSeason}`: seeded from strong co-presence ties |

Pipeline settings (`MIN_SCENES`, `STRONG_TIE`, `WEAK_TIE`, `COMMON_WORD_LIMIT`, `TITLES`, `NICKNAMES`) are at the top of `pipeline/got_pipeline.py`.

Every character account logs in with `DEMO_PASSWORD` from `.env`.

## Dataset

Jeffrey Lancaster, *game-of-thrones*: https://github.com/jeffreylancaster/game-of-thrones (used with citation, as the author requests). Character images from the dataset are not used.
