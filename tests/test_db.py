"""db.py tests against a loaded Neo4j (docker compose up -d && load_db.py --reset).

Uses throwaway users prefixed zz_test_ and removes them afterwards; seeded characters
are only read, except for follow edges the tests create and delete themselves.
"""

import pytest

import db

ME, OTHER = "zz_test_me", "zz_test_other"


@pytest.fixture(scope="module", autouse=True)
def neo4j_available():
    try:
        db.get_driver().verify_connectivity()
    except Exception as e:  # noqa: BLE001 — any connection problem means "skip", not "fail"
        pytest.skip(f"Neo4j not reachable: {e}")
    yield
    db.close()


@pytest.fixture
def users():
    cleanup = "MATCH (u:User) WHERE u.username STARTS WITH 'zz_test_' DETACH DELETE u"
    db._write(cleanup)
    assert db.create_user(ME, "Test Me", "hash", home_location="The North")
    assert db.create_user(OTHER, "Test Other", "hash")
    yield
    db._write(cleanup)


# ---------------- accounts ----------------

def test_duplicate_username_rejected(users):
    assert db.create_user(ME, "Someone Else", "hash") is False


def test_password_hash_lookup(users):
    assert db.get_password_hash(ME) == "hash"
    assert db.get_password_hash("zz_test_nobody") is None


def test_update_profile(users):
    assert db.update_profile(ME, "New Name", "new bio")
    assert db.get_user(ME)["bio"] == "new bio"
    assert db.update_profile("zz_test_nobody", "x", "y") is False


def test_locations_listed():
    assert "The North" in db.list_locations()


# ---------------- following ----------------

def test_follow_unfollow_cycle(users):
    assert db.follow(ME, OTHER) is True
    assert db.follow(ME, OTHER) is False             # no duplicate edge
    assert db.is_following(ME, OTHER)
    assert [u["username"] for u in db.get_followers(OTHER)] == [ME]
    assert [u["username"] for u in db.get_following(ME)] == [OTHER]
    assert db.unfollow(ME, OTHER) is True
    assert db.unfollow(ME, OTHER) is False           # not following → no-op, no error
    assert not db.is_following(ME, OTHER)


def test_cannot_follow_self(users):
    assert db.follow(ME, ME) is False


def test_follow_unknown_user(users):
    assert db.follow(ME, "zz_test_nobody") is False


def test_mutuals(users):
    db.follow(ME, "jon_snow")
    db.follow(OTHER, "jon_snow")
    assert [u["username"] for u in db.get_mutuals(ME, OTHER)] == ["jon_snow"]


# ---------------- profiles / search ----------------

def test_get_user_shape():
    u = db.get_user("arya_stark")
    assert set(u) == {"username", "name", "house", "bio", "sceneCount", "topLocation",
                      "isCharacter", "followers", "following"}
    assert u["house"] == "Stark" and u["isCharacter"] is True
    assert db.get_user("zz_test_nobody") is None


def test_search_case_insensitive():
    names = [u["name"] for u in db.search_users("ARYA")]
    assert "Arya Stark" in names


# ---------------- recommendations ----------------

def test_recommendations_for_main_character():
    recs = db.get_recommendations("arya_stark")
    assert len(recs) == 10
    assert set(recs[0]) == {"username", "name", "house", "mutuals", "sharedScenes",
                            "mentions", "score", "reason"}
    assert recs == sorted(recs, key=lambda r: -r["score"])
    assert all(not db.is_following("arya_stark", r["username"]) for r in recs)
    assert all(r["reason"] for r in recs)


def test_cold_start_uses_home_location(users):
    recs = db.get_recommendations(ME)
    assert recs, "a brand-new user must still get suggestions"
    assert recs[0]["reason"].startswith("often at")


def test_cold_start_without_location_falls_back_to_popular(users):
    recs = db.get_recommendations(OTHER)
    assert recs and recs[0]["reason"].startswith("popular")


# ---------------- leaderboards / paths / network ----------------

def test_popular_sorted():
    top = db.get_popular(5)
    assert [t["followers"] for t in top] == sorted((t["followers"] for t in top), reverse=True)


def test_path_endpoints():
    path = db.get_path("arya_stark", "cersei_lannister")
    assert path[0]["username"] == "arya_stark" and path[-1]["username"] == "cersei_lannister"
    assert db.get_path("arya_stark", "arya_stark") is None


def test_network_grows_with_seasons():
    early, late = db.get_network("arya_stark", season=1), db.get_network("arya_stark", season=8)
    assert len(early["nodes"]) < len(late["nodes"]) <= 51
    ids = {n["id"] for n in late["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in late["edges"])
    assert sum(n["isMe"] for n in late["nodes"]) == 1
