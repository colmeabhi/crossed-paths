# Course requirements → implementation

How ENCORE meets each requirement of the team project, and what the report needs.

## Size requirement

| Requirement | ENCORE | How |
|---|---|---|
| ≥ 1,000 nodes | **1,053** | 577 characters + 450 synthetic users + 26 locations |
| ≥ 5,000 relationships | **11,535** | CO_PRESENT 4,236 · FOLLOWS 4,215 · WAS_AT 1,417 · MENTIONED 1,428 · FAMILY 239 |

Both are checked by tests (`test_at_least_1000_nodes`, `test_database_meets_size_requirement`).

## The 13 use cases

| UC | Title | `db.py` function | Tests (`tests/test_db.py`) |
|---|---|---|---|
| 1 | User registration | `create_user(username, name, email, password_hash, bio, home_location)` | `test_uc1_*` |
| 2 | User login | `get_password_hash(username)` + `bcrypt.checkpw` in auth code | `test_uc2_*` |
| 3 | View profile | `get_user(username)` (no password field) | `test_uc3_*` |
| 4 | Edit profile | `update_profile(username, name, email, bio)` | `test_uc4_*` |
| 5 | Follow | `follow(me, them)` → `followed` / `self` / `already_following` / `not_found` | `test_uc5_*` |
| 6 | Unfollow | `unfollow(me, them)` | `test_uc6_*` |
| 7 | Following / followers | `get_following(u)`, `get_followers(u)` | `test_uc7_*` |
| 8 | Mutual connections | `get_mutuals(me, other)` | `test_uc8_*` |
| 9 | Friend recommendations | `get_recommendations(u)` (exactly two hops) | `test_uc9_*` (4 tests) |
| 10 | View all users | `get_all_users(limit, offset)`, `count_users()` | `test_uc10_*` |
| 11 | Search users | `search_users(text)` | `test_uc11_*` |
| 12 | Popular users | `get_popular(limit)` | `test_uc12_*` |
| 13 | Degrees of separation | `get_path(from, to)` (directed, max 4 hops) | `test_uc13_*` (4 tests) |

### Primary Cypher per use case (for the report)

**UC-1 Register**
```cypher
CREATE (u:User {username: $username, name: $name, email: $email, bio: $bio,
                passwordHash: $hash, house: '', isCharacter: false,
                source: 'app', createdAt: datetime()})
```
Duplicate usernames are rejected by the uniqueness constraint (`ConstraintError` → "username taken").

**UC-2 Login**
```cypher
MATCH (u:User {username: $u}) RETURN u.passwordHash AS h
```
The hash is checked in Python with `bcrypt.checkpw`. It's never displayed.

**UC-3 View profile**
```cypher
MATCH (u:User {username: $u})
RETURN u.username, u.name, u.email, u.bio,
       COUNT { (u)<-[:FOLLOWS]-() } AS followers, COUNT { (u)-[:FOLLOWS]->() } AS following
```

**UC-4 Edit profile**
```cypher
MATCH (u:User {username: $u}) SET u.name = $name, u.email = $email, u.bio = $bio
```

**UC-5 Follow**
```cypher
OPTIONAL MATCH (a:User {username: $me}) OPTIONAL MATCH (b:User {username: $them})
RETURN a IS NOT NULL AND b IS NOT NULL AS bothExist, EXISTS { (a)-[:FOLLOWS]->(b) } AS already;

MATCH (a:User {username: $me}), (b:User {username: $them}) WHERE a <> b
MERGE (a)-[f:FOLLOWS]->(b) ON CREATE SET f.since = datetime()
```
Self-follow and already-following return a status, and the UI shows an error. No relationship is created.

**UC-6 Unfollow**
```cypher
MATCH (:User {username: $me})-[f:FOLLOWS]->(:User {username: $them}) DELETE f
```

**UC-7 Following / followers** (two separate queries, because direction matters)
```cypher
MATCH (:User {username: $u})-[:FOLLOWS]->(u:User) RETURN u.username, u.name   // following
MATCH (u:User)-[:FOLLOWS]->(:User {username: $u}) RETURN u.username, u.name   // followers
```

**UC-8 Mutual connections** (a single graph pattern, not comparing lists in Python)
```cypher
MATCH (:User {username: $me})-[:FOLLOWS]->(u:User)<-[:FOLLOWS]-(:User {username: $other})
RETURN DISTINCT u.username, u.name
```

**UC-9 Friend recommendations** (exactly two hops; each person once)
```cypher
MATCH (me:User {username: $me})-[:FOLLOWS]->(a:User)-[:FOLLOWS]->(c:User)
WHERE c <> me AND NOT (me)-[:FOLLOWS]->(c)
WITH me, c, collect(DISTINCT a.name) AS via
OPTIONAL MATCH (me)-[cp:CO_PRESENT]-(c)
OPTIONAL MATCH (me)-[m:MENTIONED]->(c)
RETURN c.username, c.name, size(via) AS mutuals, via[0..3] AS via, ...score...
ORDER BY score DESC LIMIT $limit
```
The fixed-length pattern `-[:FOLLOWS]->()-[:FOLLOWS]->` matches exactly two hops. Grouping by `c` with `collect(DISTINCT a)` lists each person once. Encounter signals only **rank** the candidates; they never add anyone.

**UC-10 View all users**
```cypher
MATCH (u:User) RETURN u.username, u.name ORDER BY u.name SKIP $offset LIMIT $limit
```

**UC-11 Search** (full or partial, case-insensitive)
```cypher
MATCH (u:User)
WHERE toLower(u.name) CONTAINS toLower($q) OR u.username CONTAINS toLower($q)
RETURN u.username, u.name ORDER BY u.name LIMIT $limit
```

**UC-12 Popular users**
```cypher
MATCH (u:User)<-[:FOLLOWS]-(f)
RETURN u.username, u.name, count(f) AS followers ORDER BY followers DESC LIMIT $limit
```

**UC-13 Degrees of separation** (directed, max 4 hops)
```cypher
MATCH (a:User {username: $a}), (b:User {username: $b})
MATCH p = shortestPath((a)-[:FOLLOWS*1..4]->(b))
RETURN length(p) AS degrees, [u IN nodes(p) | u.username] AS path
```
No row means "no connection within 4 hops". Direction matters: a path A → D doesn't imply one from D → A.

## Report checklist (75 points)

- [ ] **A. Team info:** names and emails
- [ ] **B. Schema (5):** diagram with node labels, relationship types, directions and key properties. Source: [data-model.md](data-model.md).
- [ ] **C. Dataset (5):**
  - [ ] (a) name, source, URL, description: [dataset.md](dataset.md)
  - [ ] (b) final counts: **1,053 nodes, 11,535 relationships** (re-check with the counts `load_db.py` prints)
  - [ ] (c) preparation: [pipeline.md](pipeline.md), including the **public vs. synthetic table** in [dataset.md](dataset.md)
  - [ ] (d) loading: the `UNWIND $rows … MERGE` batch queries from `load_db.py`, plus the constraints
- [ ] **D. Use cases (65):** for each UC: number + title, **app screenshot** (not Neo4j Browser), the Cypher above, and a one-line explanation
- [ ] No passwords or hashes visible in any screenshot

## Source code checklist (5 points)

- [ ] `source_code/` contains everything needed to run: `app/`, `pipeline/`, `scripts/`, `tests/`, `data/csv/`, `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `README.md`
- [ ] **Excluded:** `.venv/`, `__pycache__/`, `.pytest_cache/`, `data/raw/`, `.env`, `docs/guide.html`, IDE folders

```bash
# build project.zip (run from the repo root, with report.pdf next to it)
mkdir -p /tmp/project && git archive --format=tar --prefix=source_code/ HEAD | tar -x -C /tmp/project
cp report.pdf /tmp/project/ && (cd /tmp/project && zip -r ../project.zip report.pdf source_code)
```
`git archive` only includes committed files, so ignored files and caches are left out automatically.

## Other rules

- **Independence:** the team's dataset, model and use cases must differ from each member's individual project. Confirm nobody used GoT data individually.
- **Interface:** a console is enough; ENCORE uses Streamlit (web), which is allowed. All 13 use cases must work through the app, not by typing Cypher.
