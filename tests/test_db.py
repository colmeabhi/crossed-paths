"""db.py tests against a loaded Neo4j (docker compose up -d && load_db.py --reset).

Organised by course use case (UC-1 … UC-13). Uses throwaway users prefixed zz_test_
and removes them afterwards; loaded users are only read, except for follow edges
the tests create and delete themselves.
"""

import pytest

import db

ME, OTHER, THIRD = "zz_test_me", "zz_test_other", "zz_test_third"
CHAIN = [f"zz_test_c{i}" for i in range(6)]   # c0 -> c1 -> ... -> c5
CLEANUP = "MATCH (u:User) WHERE u.username STARTS WITH 'zz_test_' DETACH DELETE u"


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
    db._write(CLEANUP)
    assert db.create_user(ME, "Test Me", "me@test.example", "hash", bio="hello", home_location="The North")
    assert db.create_user(OTHER, "Test Other", "other@test.example", "hash")
    assert db.create_user(THIRD, "Test Third", "third@test.example", "hash")
    yield
    db._write(CLEANUP)


@pytest.fixture
def chain(users):
    for u in CHAIN:
        db.create_user(u, u, f"{u}@test.example", "hash")
    for a, b in zip(CHAIN, CHAIN[1:]):
        assert db.follow(a, b) == db.FOLLOWED


def names(rows):
    return [r["username"] for r in rows]


# ---------------- size requirement ----------------

def test_database_meets_size_requirement():
    rows = db._read("MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r) AS rels")
    assert rows[0]["nodes"] >= 1000 and rows[0]["rels"] >= 5000


# ---------------- UC-1 to UC-4: accounts ----------------

def test_uc1_duplicate_username_rejected(users):
    assert db.create_user(ME, "Someone Else", "x@test.example", "hash") is False


def test_uc2_password_hash_lookup(users):
    assert db.get_password_hash(ME) == "hash"
    assert db.get_password_hash("zz_test_nobody") is None


def test_uc3_profile_has_required_fields_and_no_password(users):
    u = db.get_user(ME)
    assert (u["name"], u["username"], u["email"], u["bio"]) == ("Test Me", ME, "me@test.example", "hello")
    assert not any("password" in k.lower() for k in u)
    assert db.get_user("zz_test_nobody") is None


def test_uc4_update_profile(users):
    assert db.update_profile(ME, "New Name", "new@test.example", "new bio")
    u = db.get_user(ME)
    assert (u["username"], u["name"], u["email"], u["bio"]) == (ME, "New Name", "new@test.example", "new bio")
    assert db.update_profile("zz_test_nobody", "x", "y", "z") is False


def test_locations_listed():
    assert "The North" in db.list_locations()


# ---------------- UC-5 to UC-8: following ----------------

def test_uc5_follow_statuses(users):
    assert db.follow(ME, OTHER) == db.FOLLOWED
    assert db.follow(ME, OTHER) == db.ALREADY_FOLLOWING
    assert db.follow(ME, ME) == db.SELF
    assert db.follow(ME, "zz_test_nobody") == db.NOT_FOUND
    n = db._read("MATCH (:User {username: $a})-[f:FOLLOWS]->(:User {username: $b}) RETURN count(f) AS n",
                 a=ME, b=OTHER)[0]["n"]
    assert n == 1


def test_uc6_unfollow(users):
    db.follow(ME, OTHER)
    assert db.unfollow(ME, OTHER) is True
    assert db.unfollow(ME, OTHER) is False           # not following → no-op, no error
    assert not db.is_following(ME, OTHER)


def test_uc7_following_and_followers_are_separate(users):
    db.follow(ME, OTHER)
    assert names(db.get_following(ME)) == [OTHER]
    assert names(db.get_followers(ME)) == []
    assert names(db.get_followers(OTHER)) == [ME]
    assert names(db.get_following(OTHER)) == []


def test_uc8_mutuals(users):
    db.follow(ME, "jon_snow")
    db.follow(OTHER, "jon_snow")
    db.follow(ME, THIRD)
    assert names(db.get_mutuals(ME, OTHER)) == ["jon_snow"]


# ---------------- UC-9: recommendations ----------------

def test_uc9_two_paths_to_same_user_appear_once(users):
    # ME -> OTHER -> jon_snow  and  ME -> THIRD -> jon_snow
    for a, b in [(ME, OTHER), (ME, THIRD), (OTHER, "jon_snow"), (THIRD, "jon_snow")]:
        db.follow(a, b)
    recs = db.get_recommendations(ME, limit=100)
    assert names(recs).count("jon_snow") == 1
    jon = next(r for r in recs if r["username"] == "jon_snow")
    assert jon["mutuals"] == 2 and set(jon["via"]) == {"Test Other", "Test Third"}


def test_uc9_excludes_me_and_people_i_follow(users):
    for a, b in [(ME, OTHER), (OTHER, ME), (OTHER, THIRD), (ME, THIRD)]:
        db.follow(a, b)
    recs = names(db.get_recommendations(ME, limit=100))
    assert ME not in recs and THIRD not in recs


def test_uc9_only_exactly_two_hops(chain):
    recs = names(db.get_recommendations(CHAIN[0], limit=100))
    assert CHAIN[2] in recs                     # c0 -> c1 -> c2
    assert CHAIN[3] not in recs                 # three hops away
    assert CHAIN[1] not in recs                 # already followed


def test_uc9_every_recommendation_has_a_two_hop_path():
    for r in db.get_recommendations("arya_stark"):
        assert db._read(
            "RETURN EXISTS { (:User {username: 'arya_stark'})-[:FOLLOWS]->()-[:FOLLOWS]->(:User {username: $c}) } AS ok",
            c=r["username"])[0]["ok"]
        assert not db.is_following("arya_stark", r["username"])
        assert r["reason"].startswith("followed by")


def test_uc9_user_following_nobody_gets_none(users):
    assert db.get_recommendations(ME) == []


# ---------------- UC-10 to UC-12: exploration ----------------

def test_uc10_all_users_includes_me_and_followed(users):
    db.follow(ME, OTHER)
    everyone = names(db.get_all_users())
    assert ME in everyone and OTHER in everyone
    assert len(everyone) == db.count_users() >= 1000
    assert len(db.get_all_users(limit=25)) == 25


def test_uc11_search_partial_name_and_username():
    assert "Arya Stark" in [u["name"] for u in db.search_users("ARY")]       # partial, case-insensitive name
    assert "arya_stark" in names(db.search_users("a_sta"))                    # partial username
    assert db.search_users("   ") == []


def test_uc12_popular_sorted_by_followers():
    top = db.get_popular(10)
    counts = [t["followers"] for t in top]
    assert counts == sorted(counts, reverse=True) and counts[0] > 0


# ---------------- UC-13: degrees of separation ----------------

def test_uc13_directed_path_and_degrees(chain):
    result = db.get_path(CHAIN[0], CHAIN[3])
    assert result["degrees"] == 3
    assert names(result["path"]) == CHAIN[:4]


def test_uc13_direction_matters(chain):
    assert db.get_path(CHAIN[3], CHAIN[0]) is None


def test_uc13_max_four_hops(chain):
    assert db.get_path(CHAIN[0], CHAIN[4])["degrees"] == 4
    assert db.get_path(CHAIN[0], CHAIN[5]) is None          # 5 hops: beyond the limit


def test_uc13_same_user_is_zero():
    assert db.get_path("arya_stark", "arya_stark")["degrees"] == 0


# ---------------- extra ENCORE features ----------------

def test_encounter_suggestions_for_character():
    recs = db.get_encounter_suggestions("arya_stark")
    assert len(recs) == 10 and all(r["reason"] for r in recs)
    assert all(not db.is_following("arya_stark", r["username"]) for r in recs)


def test_encounter_cold_start_uses_home_location(users):
    recs = db.get_encounter_suggestions(ME)
    assert recs and recs[0]["reason"].startswith("often at")


def test_encounter_cold_start_without_location_falls_back_to_popular(users):
    recs = db.get_encounter_suggestions(OTHER)
    assert recs and recs[0]["reason"].startswith("popular")


def test_network_grows_with_seasons():
    early, late = db.get_network("arya_stark", season=1), db.get_network("arya_stark", season=8)
    assert len(early["nodes"]) <= len(late["nodes"]) <= 51
    ids = {n["id"] for n in late["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in late["edges"])
    assert sum(n["isMe"] for n in late["nodes"]) == 1
