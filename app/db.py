"""
ENCORE data access layer. Every Cypher query in the project lives here;
pages and auth code only call these functions.

Return conventions
- Every function returns plain Python types (dict / list / str / int / bool / None).
- A "user summary" is always {"username", "name", "house"}; house may be "".
- Functions never raise for "not found" — they return None, [] or False.

Connection settings come from .env (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD).
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------- recommendation weights (tune these) ----------------
MUTUAL_WEIGHT = 3      # per mutual friend (friend-of-a-friend) — strongest signal
MENTION_WEIGHT = 2     # per time I said their name
MENTION_CAP = 5        # mentions beyond this don't count (stops kill lists / "Reek" dominating)
SCENE_CAP = 20         # shared scenes beyond this don't count (stops Daenerys–Drogon dominating)

USER_SUMMARY = "{username: u.username, name: u.name, house: coalesce(u.house, '')}"

_driver = None


def get_driver():
    """One shared driver per process (it holds a connection pool)."""
    global _driver
    if _driver is None:
        load_dotenv(os.path.join(ROOT, ".env"))
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


def close():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def _read(query, **params):
    records, _, _ = get_driver().execute_query(query, params, routing_="r")
    return [r.data() for r in records]


def _write(query, **params):
    """Run a write; returns the result summary counters."""
    _, summary, _ = get_driver().execute_query(query, params)
    return summary.counters


# ======================================================================
# Accounts (Cypher only — hashing and sessions belong to the auth code)
# ======================================================================

def create_user(username, name, password_hash, home_location=None):
    """Register a real (non-character) user. Returns False if the username is taken.

    home_location (optional, one of list_locations()) creates a WAS_AT edge,
    which is what gives a brand-new user suggestions on day one.
    """
    try:
        _write(
            """
            CREATE (u:User {username: $username, name: $name, passwordHash: $hash,
                            house: '', bio: '', isCharacter: false, createdAt: datetime()})
            WITH u
            OPTIONAL MATCH (l:Location {name: $loc})
            FOREACH (_ IN CASE WHEN l IS NULL THEN [] ELSE [1] END |
                MERGE (u)-[:WAS_AT {count: 1, firstSeason: 1}]->(l))
            """,
            username=username, name=name, hash=password_hash, loc=home_location,
        )
        return True
    except ConstraintError:
        return False


def get_password_hash(username):
    """The stored bcrypt hash, or None if the user doesn't exist."""
    rows = _read("MATCH (u:User {username: $u}) RETURN u.passwordHash AS h", u=username)
    return rows[0]["h"] if rows else None


def update_profile(username, name, bio):
    """Returns True if the user exists and was updated."""
    c = _write("MATCH (u:User {username: $u}) SET u.name = $name, u.bio = $bio",
               u=username, name=name, bio=bio)
    return c.properties_set > 0


def list_locations():
    """All location names, sorted — for the home-location picker."""
    return [r["name"] for r in _read("MATCH (l:Location) RETURN l.name AS name ORDER BY name")]


# ======================================================================
# Profiles and search
# ======================================================================

def get_user(username):
    """Full profile, or None.

    {username, name, house, bio, sceneCount, topLocation, isCharacter, followers, following}
    """
    rows = _read(
        """
        MATCH (u:User {username: $u})
        RETURN u.username AS username, u.name AS name, coalesce(u.house, '') AS house,
               coalesce(u.bio, '') AS bio, coalesce(u.sceneCount, 0) AS sceneCount,
               coalesce(u.topLocation, '') AS topLocation, u.isCharacter AS isCharacter,
               COUNT { (u)<-[:FOLLOWS]-() } AS followers,
               COUNT { (u)-[:FOLLOWS]->() } AS following
        """,
        u=username,
    )
    return rows[0] if rows else None


def search_users(text, limit=20):
    """Case-insensitive name or username search → [user summary]."""
    return [r["user"] for r in _read(
        f"""
        MATCH (u:User)
        WHERE toLower(u.name) CONTAINS toLower($q) OR u.username CONTAINS toLower($q)
        RETURN {USER_SUMMARY} AS user ORDER BY u.name LIMIT $limit
        """,
        q=text.strip(), limit=limit,
    )]


# ======================================================================
# Following
# ======================================================================

def follow(me, them):
    """Returns True if a new follow was created (False: self-follow, already following, or unknown user)."""
    c = _write(
        """
        MATCH (a:User {username: $me}), (b:User {username: $them})
        WHERE a <> b
        MERGE (a)-[f:FOLLOWS]->(b)
        ON CREATE SET f.since = datetime()
        """,
        me=me, them=them,
    )
    return c.relationships_created > 0


def unfollow(me, them):
    """Returns True if a follow was removed. Unfollowing someone you don't follow is a no-op."""
    c = _write("MATCH (:User {username: $me})-[f:FOLLOWS]->(:User {username: $them}) DELETE f",
               me=me, them=them)
    return c.relationships_deleted > 0


def is_following(me, them):
    rows = _read("RETURN EXISTS { (:User {username: $me})-[:FOLLOWS]->(:User {username: $them}) } AS f",
                 me=me, them=them)
    return rows[0]["f"]


def get_followers(username):
    """People who follow `username` → [user summary]."""
    return [r["user"] for r in _read(
        f"MATCH (u:User)-[:FOLLOWS]->(:User {{username: $u}}) RETURN {USER_SUMMARY} AS user ORDER BY u.name",
        u=username)]


def get_following(username):
    """People `username` follows → [user summary]."""
    return [r["user"] for r in _read(
        f"MATCH (:User {{username: $u}})-[:FOLLOWS]->(u:User) RETURN {USER_SUMMARY} AS user ORDER BY u.name",
        u=username)]


def get_mutuals(me, other):
    """People both `me` and `other` follow → [user summary]."""
    return [r["user"] for r in _read(
        f"""
        MATCH (:User {{username: $me}})-[:FOLLOWS]->(u:User)<-[:FOLLOWS]-(:User {{username: $other}})
        RETURN {USER_SUMMARY} AS user ORDER BY u.name
        """,
        me=me, other=other)]


# ======================================================================
# Recommendations
# ======================================================================

def _reason(r):
    parts = []
    if r["mutuals"]:
        parts.append(f"{r['mutuals']} mutual friend{'s' if r['mutuals'] != 1 else ''}")
    if r["sharedScenes"]:
        parts.append(f"shared {r['sharedScenes']} scene{'s' if r['sharedScenes'] != 1 else ''}")
    if r["mentions"]:
        parts.append(f"you mentioned them {r['mentions']} time{'s' if r['mentions'] != 1 else ''}")
    return " · ".join(parts)


def get_recommendations(username, limit=10):
    """People to follow, best first.

    → [{username, name, house, mutuals, sharedScenes, mentions, score, reason}]
    Candidates: friends-of-friends or anyone I shared a scene with.
    New users with no signals fall back to people often at their home location, then popular users.
    """
    rows = _read(
        """
        MATCH (me:User {username: $me})
        MATCH (c:User)
        WHERE c <> me
          AND NOT (me)-[:FOLLOWS]->(c)
          AND ( EXISTS { (me)-[:FOLLOWS]->()-[:FOLLOWS]->(c) } OR EXISTS { (me)-[:CO_PRESENT]-(c) } )
        WITH me, c, COUNT { (me)-[:FOLLOWS]->()-[:FOLLOWS]->(c) } AS mutuals
        OPTIONAL MATCH (me)-[cp:CO_PRESENT]-(c)
        OPTIONAL MATCH (me)-[m:MENTIONED]->(c)
        WITH c, mutuals, coalesce(cp.count, 0) AS sharedScenes, coalesce(m.count, 0) AS mentions
        RETURN c.username AS username, c.name AS name, coalesce(c.house, '') AS house,
               mutuals, sharedScenes, mentions,
               mutuals * $mw
                 + CASE WHEN mentions > $mcap THEN $mcap ELSE mentions END * $nw
                 + CASE WHEN sharedScenes > $scap THEN $scap ELSE sharedScenes END AS score
        ORDER BY score DESC, name
        LIMIT $limit
        """,
        me=username, limit=limit, mw=MUTUAL_WEIGHT, nw=MENTION_WEIGHT, mcap=MENTION_CAP, scap=SCENE_CAP,
    )
    for r in rows:
        r["reason"] = _reason(r)
    if rows:
        return rows
    return _cold_start(username, limit)


def _cold_start(username, limit):
    """For users with no follows or scenes yet: regulars at my locations, then popular users."""
    rows = _read(
        """
        MATCH (me:User {username: $me})-[:WAS_AT]->(l:Location)<-[w:WAS_AT]-(c:User)
        WHERE c <> me AND NOT (me)-[:FOLLOWS]->(c)
        WITH c, l, w ORDER BY w.count DESC
        WITH c, collect(l.name)[0] AS place, sum(w.count) AS visits
        RETURN c.username AS username, c.name AS name, coalesce(c.house, '') AS house,
               visits AS score, 'often at ' + place AS reason
        ORDER BY score DESC LIMIT $limit
        """,
        me=username, limit=limit,
    )
    if len(rows) < limit:
        seen = {r["username"] for r in rows} | {username}
        for p in get_popular(limit * 2):
            if p["username"] not in seen and len(rows) < limit and not is_following(username, p["username"]):
                rows.append({"username": p["username"], "name": p["name"], "house": p["house"],
                             "score": 0, "reason": f"popular · {p['followers']} followers"})
    for r in rows:
        r.update(mutuals=0, sharedScenes=0, mentions=0)
    return rows


# ======================================================================
# Leaderboards and paths
# ======================================================================

def get_popular(limit=10):
    """Most-followed users → [{username, name, house, followers}]."""
    return _read(
        """
        MATCH (u:User)<-[:FOLLOWS]-(f)
        RETURN u.username AS username, u.name AS name, coalesce(u.house, '') AS house,
               count(f) AS followers
        ORDER BY followers DESC, name LIMIT $limit
        """,
        limit=limit)


def get_most_connected(limit=10):
    """Characters who shared scenes with the most others → [{username, name, house, connections}]."""
    return _read(
        """
        MATCH (u:User)-[:CO_PRESENT]-(o)
        RETURN u.username AS username, u.name AS name, coalesce(u.house, '') AS house,
               count(DISTINCT o) AS connections
        ORDER BY connections DESC, name LIMIT $limit
        """,
        limit=limit)


def get_path(me, other, max_hops=6):
    """Shortest follow chain (either direction) → [user summary, ...] from me to other, or None."""
    if me == other:
        return None
    rows = _read(
        f"""
        MATCH (a:User {{username: $me}}), (b:User {{username: $other}})
        MATCH p = shortestPath((a)-[:FOLLOWS*..{int(max_hops)}]-(b))
        RETURN [u IN nodes(p) | {USER_SUMMARY}] AS path
        """,
        me=me, other=other)
    return rows[0]["path"] if rows else None


# ======================================================================
# Network view
# ======================================================================

def get_network(username, season=8, limit=50, min_count=15):
    """My neighborhood as of `season` (1–8), ready for pyvis / streamlit-agraph.

    → {"nodes": [{id, name, house, isMe}],
       "edges": [{source, target, type, count}]}   type: CO_PRESENT | FOLLOWS | MENTIONED
    Neighbors = people I shared scenes with or follow / am followed by, strongest first, capped at `limit`.
    All my own edges are included; between two neighbors only CO_PRESENT with count >= min_count
    is drawn, to avoid a hairball.
    Follows made in the app (no season) show at every season. Counts are whole-show totals
    (the pipeline stores firstSeason, not per-season counts); MENTIONED has no season.
    """
    neighbors = _read(
        """
        MATCH (me:User {username: $u})
        OPTIONAL MATCH (me)-[r:CO_PRESENT]-(c) WHERE r.firstSeason <= $s
        WITH me, collect({n: c, w: r.count}) AS a
        OPTIONAL MATCH (me)-[f:FOLLOWS]-(o) WHERE coalesce(f.sinceSeason, 1) <= $s
        WITH a, collect({n: o, w: 1}) AS b
        UNWIND a + b AS x
        WITH x WHERE x.n IS NOT NULL
        WITH x.n AS n, max(x.w) AS w ORDER BY w DESC LIMIT $limit
        RETURN n.username AS username
        """,
        u=username, s=season, limit=limit)
    members = [username] + [r["username"] for r in neighbors]

    nodes = _read(
        """
        MATCH (u:User) WHERE u.username IN $members
        RETURN u.username AS id, u.name AS name, coalesce(u.house, '') AS house, u.username = $me AS isMe
        """,
        members=members, me=username)

    edges = _read(
        """
        MATCH (a:User)-[r:CO_PRESENT|FOLLOWS|MENTIONED]->(b:User)
        WHERE a.username IN $members AND b.username IN $members
          AND CASE type(r)
                WHEN 'CO_PRESENT' THEN r.firstSeason <= $s
                WHEN 'FOLLOWS' THEN coalesce(r.sinceSeason, 1) <= $s
                ELSE true END
          AND (a.username = $me OR b.username = $me
               OR (type(r) = 'CO_PRESENT' AND r.count >= $minc))
        RETURN a.username AS source, b.username AS target, type(r) AS type, coalesce(r.count, 1) AS count
        """,
        members=members, me=username, s=season, minc=min_count)
    return {"nodes": nodes, "edges": edges}
