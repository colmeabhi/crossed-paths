# ENCORE: Crossed Paths

A social network that suggests connections from shared places, shared scenes and name mentions. Neo4j back end, Game of Thrones characters as users.

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python ≥ 3.10 and all packages)
- Docker Desktop (for the local Neo4j database)
- git

## Run it

```bash
uv sync                                      # 1. install packages
cp .env.example .env                         # 2. set NEO4J_PASSWORD in .env
./scripts/fetch_data.sh                      # 3. download the raw dataset
uv run python pipeline/got_pipeline.py       # 4. build the CSVs in data/csv/
docker compose up -d                         # 5. start Neo4j
uv run python pipeline/load_db.py --reset    # 6. load the CSVs into Neo4j
uv run pytest                                # 7. check everything (26 tests)
```

Steps 3–4 are optional: the CSVs are already committed, so you can go straight from step 2 to step 5.

- **Neo4j Browser:** http://localhost:7474 (log in with the user and password from `.env`)
- **Demo login:** any character, e.g. `arya_stark`, with the `DEMO_PASSWORD` from `.env`
- **Rebuild the data:** rerun step 6. Users who signed up through the app are kept.
- **Stop Neo4j:** `docker compose down` (add `-v` to delete its data)

## Using AuraDB instead of Docker

Put your AuraDB URI (`neo4j+s://…`), user and password in `.env`, skip step 5, and run step 6.

## Dataset

Jeffrey Lancaster, *game-of-thrones*: https://github.com/jeffreylancaster/game-of-thrones
