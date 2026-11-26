# ENCORE documentation

ENCORE (**ENC**ounter-based c**O**nnection **RE**commendation) is a social network that suggests people to follow from *ambient* signals: being in the same place, sharing a scene, and saying someone's name. Game of Thrones characters are the demo users. The back end is Neo4j and the front end is Streamlit.

For running the project, see the [root README](../README.md).

## Start here

| If you want to… | Read |
|---|---|
| Check the course requirements / write the report | [requirements.md](requirements.md) |
| Understand how the pieces fit | [architecture.md](architecture.md) |
| See what's in the database | [data-model.md](data-model.md) |
| Call the back end from a page or from auth code | [db-api.md](db-api.md) |
| Understand how suggestions are scored | [recommendations.md](recommendations.md) |
| Change or rerun the data pipeline | [pipeline.md](pipeline.md) |
| Know where the data comes from and its limits | [dataset.md](dataset.md) |
| Add code without breaking things | [contributing.md](contributing.md) |

## Project at a glance

| | |
|---|---|
| Users | 577 GoT characters + 450 synthetic users + anyone who registers |
| Locations | 26 regions (The North, The Crownlands, …) |
| Nodes | 1,053 (1,027 users + 26 locations); course minimum 1,000 |
| Relationships | 11,535 across 5 types; course minimum 5,000 · `CO_PRESENT`, `WAS_AT`, `MENTIONED`, `FOLLOWS`, `FAMILY` |
| Stack | Python · Neo4j 5 · Streamlit · uv · Docker / AuraDB |
| Tests | 41 (16 on pipeline output, 25 on the live database, grouped by use case) |

## Who owns what

| Area | Files |
|---|---|
| Data pipeline + graph back end | `pipeline/`, `app/db.py`, `tests/` |
| Auth (bcrypt, sessions, register/login pages) | auth code calling `app/db.py` account functions |
| UI pages + network visualization | `app/Home.py`, `app/pages/` (planned) |
