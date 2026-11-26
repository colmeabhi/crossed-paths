# `app/db.py` API reference

`app/db.py` is the **only** place with Cypher. Pages and auth code import it and call functions:

```python
import db

recs = db.get_recommendations(st.session_state["username"])
```

The course use-case number (UC-n) is shown next to each function. See [requirements.md](requirements.md) for the Cypher behind each one.

## Conventions

- Every function returns **plain Python types** (`dict`, `list`, `str`, `int`, `bool`, `None`), never Neo4j objects.
- A **user summary** is always `{"username": str, "name": str, "house": str}`. `house` may be `""`.
- "Not found" never raises. Functions return `None`, `[]` or a status value instead.
- Passwords are never returned. The only exception is the hash from `get_password_hash`, which exists for login and must never be displayed.
- Connection settings come from `.env`. One shared driver is created on first use; call `db.close()` on shutdown if needed.

## UC-1 to UC-4: accounts

Queries only. Password hashing (bcrypt) and sessions belong to the auth code.

| UC | Function | Returns | Notes |
|---|---|---|---|
| 1 | `create_user(username, name, email, password_hash, bio="", home_location=None)` | `bool` | `False` if the username is taken. `home_location` (from `list_locations()`) creates a `WAS_AT` edge used by encounter suggestions. |
| 2 | `get_password_hash(username)` | `str \| None` | `None` if the user doesn't exist. |
| 3 | `get_user(username)` | `dict \| None` | `{username, name, email, bio, house, sceneCount, topLocation, isCharacter, source, followers, following}`. No password. |
| 4 | `update_profile(username, name, email, bio)` | `bool` | The username can't change. `False` if the user doesn't exist. |
| – | `list_locations()` | `list[str]` | Sorted region names for the registration dropdown. |

```python
# auth code sketch
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
if not db.create_user(username, name, email, hashed, bio=bio, home_location=region):
    st.error("That username is already taken.")

stored = db.get_password_hash(username)
if stored and bcrypt.checkpw(password.encode(), stored.encode()):
    st.session_state["username"] = username
else:
    st.error("Wrong username or password.")
```

## UC-5 to UC-8: following

| UC | Function | Returns | Notes |
|---|---|---|---|
| 5 | `follow(me, them)` | `str` | One of `db.FOLLOWED`, `db.SELF`, `db.ALREADY_FOLLOWING`, `db.NOT_FOUND`. Only `FOLLOWED` creates a relationship. |
| 6 | `unfollow(me, them)` | `bool` | `True` if a follow was removed. Unfollowing someone you don't follow is a harmless no-op. |
| 7 | `get_following(username)` | `[user summary]` | people `username` follows |
| 7 | `get_followers(username)` | `[user summary]` | people who follow `username` |
| 8 | `get_mutuals(me, other)` | `[user summary]` | people **both** of them follow |
| – | `is_following(me, them)` | `bool` | for showing Follow vs. Unfollow buttons |

```python
# UC-5 error messages
MESSAGES = {
    db.FOLLOWED: ("success", "You now follow {them}."),
    db.SELF: ("error", "You can't follow yourself."),
    db.ALREADY_FOLLOWING: ("error", "You already follow {them}."),
    db.NOT_FOUND: ("error", "User {them} doesn't exist."),
}
kind, text = MESSAGES[db.follow(me, them)]
getattr(st, kind)(text.format(them=them))
```

## UC-9: friend recommendations

| Function | Returns |
|---|---|
| `get_recommendations(username, limit=10)` | `[{username, name, house, mutuals, via, sharedScenes, mentions, score, reason}]`, best first |

- **Only people exactly two follow hops away** (me → A → B). Excludes me and people I already follow. Each person appears once, however many paths lead to them.
- `mutuals` = how many people I follow who follow them. `via` = up to 3 of those people's names.
- `reason` is ready to display: `"followed by Sandor Clegane, Gendry, Jon Snow · shared 18 scenes"`.
- Returns `[]` if the user follows nobody. Show "Follow some people to get recommendations" and link to Popular or Encounters.

## UC-10 to UC-12: exploration

| UC | Function | Returns | Notes |
|---|---|---|---|
| 10 | `get_all_users(limit=None, offset=0)` | `[user summary]` | Everyone, including me and people I follow, sorted by name. There are 1,000+ users, so page through with `limit`/`offset`. |
| 10 | `count_users()` | `int` | total, for page counts |
| 11 | `search_users(text, limit=50)` | `[user summary]` | Full or partial, case-insensitive match on name **or** username. Blank text gives `[]`. |
| 12 | `get_popular(limit=10)` | `[{username, name, house, followers}]` | most followed first |

## UC-13: degrees of separation

| Function | Returns |
|---|---|
| `get_path(from_user, to_user, max_hops=4)` | `{"degrees": int, "path": [user summary, …]}` or `None` |

- Follows the **direction** of follows: a path A → D doesn't imply D → A.
- `None` means no connection within 4 hops, **or** a username doesn't exist. Call `get_user` on both first to tell those apart.
- The same user twice gives `degrees: 0`.

## Extra ENCORE features (beyond the 13 use cases)

| Function | Returns | Notes |
|---|---|---|
| `get_encounter_suggestions(username, limit=10)` | `[{username, name, house, sharedScenes, mentions, score, reason}]` | "People you've crossed paths with": based on shared scenes and name mentions, not follows. New users get regulars at their home region, then popular users. |
| `get_most_connected(limit=10)` | `[{username, name, house, connections}]` | characters with the most distinct scene partners |
| `get_network(username, season=8, limit=50, min_count=15)` | `{"nodes": [{id, name, house, isMe}], "edges": [{source, target, type, count}]}` | neighborhood graph for the season slider |

Network notes:
- `id`, `source` and `target` are usernames. `type` is `CO_PRESENT`, `FOLLOWS` or `MENTIONED`.
- All of the user's own edges are included. Between neighbors, only `CO_PRESENT` with `count >= min_count` is drawn (no hairball).
- `season` (1–8) hides scene links and seeded follows that started later. App and synthetic follows show at every season.

```python
from pyvis.network import Network

net = Network(height="600px", directed=True)
data = db.get_network(me, season=st.slider("Season", 1, 8, 8))
for n in data["nodes"]:
    net.add_node(n["id"], label=n["name"], color="#3b5bdb" if n["isMe"] else None)
for e in data["edges"]:
    net.add_edge(e["source"], e["target"], value=e["count"], dashes=e["type"] == "MENTIONED")
```

## Need something that isn't here?

Ask the back-end owner to add a function to `db.py`, rather than writing Cypher in a page. See [contributing.md](contributing.md).
