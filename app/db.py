"""
ENCORE data access layer. Every Cypher query in the project lives here;
pages and auth code only call these functions.

Use cases (course requirements) → function
  UC-1  Register              create_user
  UC-2  Login                 get_password_hash        (bcrypt check happens in auth code)
  UC-3  View profile          get_user
  UC-4  Edit profile          update_profile
  UC-5  Follow                follow
  UC-6  Unfollow              unfollow
  UC-7  Following/followers   get_following, get_followers
  UC-8  Mutual connections    get_mutuals
  UC-9  Recommendations       get_recommendations      (strict two-hop follow paths)
  UC-10 View all users        get_all_users, count_users
  UC-11 Search users          search_users
  UC-12 Popular users         get_popular
  UC-13 Degrees of separation get_path                 (directed, max 4 hops)
  Extra (ENCORE)              get_encounter_suggestions, get_network, get_most_connected

Return conventions
- Every function returns plain Python types (dict / list / str / int / bool / None).
- A "user summary" is always {"username", "name", "house"}; house may be "".
- Functions never raise for "not found" — they return None, [] or a status value.
- Passwords are never returned, except the hash from get_password_hash (for login only).

Connection settings come from .env (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD).
"""

import os

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------- ranking weights (tune these) ----------------
MUTUAL_WEIGHT = 3      # per mutual connection (people I follow who follow them) — strongest signal
MENTION_WEIGHT = 2     # per time I said their name
MENTION_CAP = 5        # mentions beyond this don't count (stops kill lists / "Reek" dominating)
SCENE_CAP = 20         # shared scenes beyond this don't count (stops Daenerys–Drogon dominating)
MAX_SEPARATION = 4     # UC-13: longest follow path searched

# follow() results
FOLLOWED, SELF, ALREADY_FOLLOWING, NOT_FOUND = "followed", "self", "already_following", "not_found"

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


def _summaries(query, **params):
    """Run a query that returns `... AS user` rows → [user summary]."""
    return [r["user"] for r in _read(query, **params)]


# ======================================================================
# UC-1 to UC-4: accounts (Cypher only — hashing and sessions belong to the auth code)
# ======================================================================

def create_user(username, name, email, password_hash, bio="", home_location=None):
    """UC-1. Register a new app user. Returns False if the username is taken.

    home_location (optional, one of list_locations()) creates a WAS_AT edge,
    which powers encounter suggestions for brand-new users.
    """
    try:
        _write(
            """
            CREATE (u:User {username: $username, name: $name, email: $email, bio: $bio,
                            passwordHash: $hash, house: '', isCharacter: false,
                            source: 'app', createdAt: datetime()})
            WITH u
            OPTIONAL MATCH (l:Location {name: $loc})
            FOREACH (_ IN CASE WHEN l IS NULL THEN [] ELSE [1] END |
                MERGE (u)-[:WAS_AT {count: 1}]->(l))
            """,
            username=username, name=name, email=email, bio=bio or "", hash=password_hash, loc=home_location,
        )
        return True
    except ConstraintError:
        return False


def get_password_hash(username):
    """UC-2. The stored bcrypt hash, or None if the user doesn't exist. Never display it."""
    rows = _read("MATCH (u:User {username: $u}) RETURN u.passwordHash AS h", u=username)
    return rows[0]["h"] if rows else None


def get_user(username):
    """UC-3. Profile (no password), or None.

    {username, name, email, bio, house, sceneCount, topLocation, isCharacter, source, followers, following}
    """
    rows = _read(
        """
        MATCH (u:User {username: $u})
        RETURN u.username AS username, u.name AS name, coalesce(u.email, '') AS email,
               coalesce(u.bio, '') AS bio, coalesce(u.house, '') AS house,
               coalesce(u.sceneCount, 0) AS sceneCount, coalesce(u.topLocation, '') AS topLocation,
               coalesce(u.isCharacter, false) AS isCharacter, coalesce(u.source, 'app') AS source,
               COUNT { (u)<-[:FOLLOWS]-() } AS followers,
               COUNT { (u)-[:FOLLOWS]->() } AS following
        """,
        u=username,
    )
    return rows[0] if rows else None


def update_profile(username, name, email, bio):
    """UC-4. Update name, email and bio (the username never changes). True if the user exists."""
    c = _write("MATCH (u:User {username: $u}) SET u.name = $name, u.email = $email, u.bio = $bio",
               u=username, name=name, email=email, bio=bio or "")
    return c.properties_set > 0


def list_locations():
    """All location names, sorted — for the home-location picker at registration."""
    return [r["name"] for r in _read("MATCH (l:Location) RETURN l.name AS name ORDER BY name")]


# ======================================================================
# UC-5 to UC-8: following
# ======================================================================

def follow(me, them):
    """UC-5. Returns FOLLOWED, SELF, ALREADY_FOLLOWING or NOT_FOUND.

    Only FOLLOWED creates a relationship; the UI shows an error message for the others.
    """
    if me == them:
        return SELF
    rows = _read(
        """
        OPTIONAL MATCH (a:User {username: $me})
        OPTIONAL MATCH (b:User {username: $them})
        RETURN a IS NOT NULL AND b IS NOT NULL AS bothExist,
               EXISTS { (a)-[:FOLLOWS]->(b) } AS already
        """,
        me=me, them=them)
    if not rows[0]["bothExist"]:
        return NOT_FOUND
    if rows[0]["already"]:
        return ALREADY_FOLLOWING
    c = _write(
        """
        MATCH (a:User {username: $me}), (b:User {username: $them})
        WHERE a <> b
        MERGE (a)-[f:FOLLOWS]->(b)
        ON CREATE SET f.since = datetime()
        """,
        me=me, them=them,
    )
    # MERGE also guards against a double-click race between the check and the write
    return FOLLOWED if c.relationships_created > 0 else ALREADY_FOLLOWING


def unfollow(me, them):
    """UC-6. True if a follow was removed. Unfollowing someone you don't follow is a no-op."""
    c = _write("MATCH (:User {username: $me})-[f:FOLLOWS]->(:User {username: $them}) DELETE f",
               me=me, them=them)
    return c.relationships_deleted > 0


def is_following(me, them):
    rows = _read("RETURN EXISTS { (:User {username: $me})-[:FOLLOWS]->(:User {username: $them}) } AS f",
                 me=me, them=them)
    return rows[0]["f"]


def get_following(username):
    """UC-7. People `username` follows → [user summary]."""
    return _summaries(
        f"MATCH (:User {{username: $u}})-[:FOLLOWS]->(u:User) RETURN {USER_SUMMARY} AS user ORDER BY u.name",
        u=username)


def get_followers(username):
    """UC-7. People who follow `username` → [user summary]."""
    return _summaries(
        f"MATCH (u:User)-[:FOLLOWS]->(:User {{username: $u}}) RETURN {USER_SUMMARY} AS user ORDER BY u.name",
        u=username)


def get_mutuals(me, other):
    """UC-8. People followed by both `me` and `other` → [user summary] (one graph pattern)."""
    return _summaries(
        f"""
        MATCH (:User {{username: $me}})-[:FOLLOWS]->(u:User)<-[:FOLLOWS]-(:User {{username: $other}})
        RETURN DISTINCT {USER_SUMMARY} AS user ORDER BY user.name
        """,
        me=me, other=other)


# ======================================================================
# UC-9: recommendations (strict two-hop)
# ======================================================================

def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def get_recommendations(username, limit=10):
    """UC-9. People reachable by exactly two follow hops (me → A → B), best first.

    → [{username, name, house, mutuals, via, sharedScenes, mentions, score, reason}]
    Excludes me and people I already follow; each person appears once however many paths lead there.
    `mutuals` = how many people I follow who follow them; `via` = up to 3 of their names.
    ENCORE's encounter signals (shared scenes, mentions) only change the ORDER, never who is included.
    Returns [] for a user who follows nobody.
    """
    rows = _read(
        """
        MATCH (me:User {username: $me})-[:FOLLOWS]->(a:User)-[:FOLLOWS]->(c:User)
        WHERE c <> me AND NOT (me)-[:FOLLOWS]->(c)
        WITH me, c, collect(DISTINCT a.name) AS via
        OPTIONAL MATCH (me)-[cp:CO_PRESENT]-(c)
        OPTIONAL MATCH (me)-[m:MENTIONED]->(c)
        WITH c, size(via) AS mutuals, via[0..3] AS via,
             coalesce(cp.count, 0) AS sharedScenes, coalesce(m.count, 0) AS mentions
        RETURN c.username AS username, c.name AS name, coalesce(c.house, '') AS house,
               mutuals, via, sharedScenes, mentions,
               mutuals * $mw
                 + CASE WHEN mentions > $mcap THEN $mcap ELSE mentions END * $nw
                 + CASE WHEN sharedScenes > $scap THEN $scap ELSE sharedScenes END AS score
        ORDER BY score DESC, name
        LIMIT $limit
        """,
        me=username, limit=limit, mw=MUTUAL_WEIGHT, nw=MENTION_WEIGHT, mcap=MENTION_CAP, scap=SCENE_CAP,
    )
    for r in rows:
        extra = r["mutuals"] - len(r["via"])
        parts = ["followed by " + ", ".join(r["via"]) + (f" +{extra} more" if extra > 0 else "")]
        if r["sharedScenes"]:
            parts.append("shared " + _plural(r["sharedScenes"], "scene"))
        if r["mentions"]:
            parts.append("you mentioned them " + _plural(r["mentions"], "time"))
        r["reason"] = " · ".join(parts)
    return rows


# ======================================================================
# UC-10 to UC-12: search and exploration
# ======================================================================

def count_users():
    return _read("MATCH (u:User) RETURN count(u) AS n")[0]["n"]


def get_all_users(limit=None, offset=0):
    """UC-10. Every user (including me and people I follow), sorted by name → [user summary].

    Pass limit/offset to page through the list (there are 1,000+ users).
    """
    return _summaries(
        f"""
        MATCH (u:User)
        RETURN {USER_SUMMARY} AS user ORDER BY u.name, u.username
        SKIP $offset LIMIT $limit
        """,
        offset=offset, limit=limit if limit is not None else 1_000_000)


def search_users(text, limit=50):
    """UC-11. Full or partial, case-insensitive match on name or username → [user summary]."""
    q = text.strip()
    if not q:
        return []
    return _summaries(
        f"""
        MATCH (u:User)
        WHERE toLower(u.name) CONTAINS toLower($q) OR u.username CONTAINS toLower($q)
        RETURN {USER_SUMMARY} AS user ORDER BY u.name LIMIT $limit
        """,
        q=q, limit=limit)


def get_popular(limit=10):
    """UC-12. Most-followed users first → [{username, name, house, followers}]."""
    return _read(
        """
        MATCH (u:User)<-[:FOLLOWS]-(f)
        RETURN u.username AS username, u.name AS name, coalesce(u.house, '') AS house,
               count(f) AS followers
        ORDER BY followers DESC, name LIMIT $limit
        """,
        limit=limit)


# ======================================================================
# UC-13: degrees of separation
# ======================================================================

def get_path(from_user, to_user, max_hops=MAX_SEPARATION):
    """UC-13. Shortest DIRECTED follow path from_user → … → to_user, up to max_hops.

    → {"degrees": int, "path": [user summary, ...]} or None if no path within the limit
    (or either user doesn't exist — check with get_user to tell those apart).
    The same user gives degrees 0.
    """
    if from_user == to_user:
        u = get_user(from_user)
        return {"degrees": 0, "path": [{k: u[k] for k in ("username", "name", "house")}]} if u else None
    rows = _read(
        f"""
        MATCH (a:User {{username: $a}}), (b:User {{username: $b}})
        MATCH p = shortestPath((a)-[:FOLLOWS*1..{int(max_hops)}]->(b))
        RETURN length(p) AS degrees, [u IN nodes(p) | {USER_SUMMARY}] AS path
        """,
        a=from_user, b=to_user)
    return rows[0] if rows else None


# ======================================================================
# Extra ENCORE features (beyond the 13 use cases)
# ======================================================================

def get_encounter_suggestions(username, limit=10):
    """"People you've crossed paths with": suggestions from sensed signals, not follows.

    → [{username, name, house, sharedScenes, mentions, score, reason}]
    Candidates: anyone I shared a scene with or mentioned, not already followed.
    New users with neither fall back to regulars at their home location, then popular users.
    """
    rows = _read(
        """
        MATCH (me:User {username: $me})
        MATCH (c:User)
        WHERE c <> me AND NOT (me)-[:FOLLOWS]->(c)
          AND (EXISTS { (me)-[:CO_PRESENT]-(c) } OR EXISTS { (me)-[:MENTIONED]->(c) })
        OPTIONAL MATCH (me)-[cp:CO_PRESENT]-(c)
        OPTIONAL MATCH (me)-[m:MENTIONED]->(c)
        WITH c, coalesce(cp.count, 0) AS sharedScenes, coalesce(m.count, 0) AS mentions
        RETURN c.username AS username, c.name AS name, coalesce(c.house, '') AS house,
               sharedScenes, mentions,
               CASE WHEN mentions > $mcap THEN $mcap ELSE mentions END * $nw
                 + CASE WHEN sharedScenes > $scap THEN $scap ELSE sharedScenes END AS score
        ORDER BY score DESC, name
        LIMIT $limit
        """,
        me=username, limit=limit, nw=MENTION_WEIGHT, mcap=MENTION_CAP, scap=SCENE_CAP,
    )
    for r in rows:
        parts = []
        if r["sharedScenes"]:
            parts.append("shared " + _plural(r["sharedScenes"], "scene"))
        if r["mentions"]:
            parts.append("you mentioned them " + _plural(r["mentions"], "time"))
        r["reason"] = " · ".join(parts)
    return rows or _cold_start(username, limit)


def _cold_start(username, limit):
    """For users with no encounters yet: regulars at my locations, then popular users."""
    rows = _read(
        """
        MATCH (me:User {username: $me})-[:WAS_AT]->(l:Location)<-[w:WAS_AT]-(c:User)
        WHERE c <> me AND NOT (me)-[:FOLLOWS]->(c)
        WITH c, l, w ORDER BY w.count DESC
        WITH c, collect(l.name)[0] AS place, sum(w.count) AS visits
        RETURN c.username AS username, c.name AS name, coalesce(c.house, '') AS house,
               visits AS score, 'often at ' + place AS reason
        ORDER BY score DESC, name LIMIT $limit
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
        r.update(sharedScenes=0, mentions=0)
    return rows


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


def get_network(username, season=8, limit=50, min_count=15):
    """My neighborhood as of `season` (1–8), ready for pyvis / streamlit-agraph.

    → {"nodes": [{id, name, house, isMe}],
       "edges": [{source, target, type, count}]}   type: CO_PRESENT | FOLLOWS | MENTIONED
    Neighbors = people I shared scenes with or follow / am followed by, strongest first, capped at `limit`.
    All my own edges are included; between two neighbors only CO_PRESENT with count >= min_count
    is drawn, to avoid a hairball.
    Follows made in the app or generated (no season) show at every season. Counts are whole-show totals
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
