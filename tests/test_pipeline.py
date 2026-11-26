"""Data sanity checks on the pipeline output. Run after `python pipeline/got_pipeline.py`."""

import csv
import os

import pytest

CSV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "csv")

RELATIONSHIP_FILES = ["co_present.csv", "was_at.csv", "mentioned.csv", "family.csv",
                      "follows_seed.csv", "synthetic_follows.csv"]


def read(name):
    path = os.path.join(CSV_DIR, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} missing — run pipeline/got_pipeline.py first")
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pair_count(rows, a, b):
    for r in rows:
        if {r["user_a"], r["user_b"]} == {a, b}:
            return int(r["count"])
    return 0


# ---------------- course size requirement ----------------

def test_at_least_1000_nodes():
    nodes = len(read("users.csv")) + len(read("synthetic_users.csv")) + len(read("locations.csv"))
    assert nodes >= 1000


def test_at_least_5000_relationships():
    rels = sum(len(read(f)) for f in RELATIONSHIP_FILES) + len(read("synthetic_users.csv"))  # + home WAS_AT
    assert rels >= 5000


# ---------------- shape ----------------

def test_sizes_in_expected_range():
    assert 560 <= len(read("users.csv")) <= 600
    assert len(read("synthetic_users.csv")) == 450
    assert len(read("locations.csv")) == 26
    assert 4000 <= len(read("co_present.csv")) <= 4500
    assert 900 <= len(read("was_at.csv")) <= 1050
    assert 1300 <= len(read("mentioned.csv")) <= 1600


def test_usernames_unique_across_real_and_synthetic():
    names = [r["username"] for r in read("users.csv")] + [r["username"] for r in read("synthetic_users.csv")]
    assert len(names) == len(set(names))


def test_every_user_has_an_email():
    for r in read("users.csv") + read("synthetic_users.csv"):
        assert "@" in r["email"]


def test_every_edge_endpoint_exists():
    users = {r["username"] for r in read("users.csv")} | {r["username"] for r in read("synthetic_users.csv")}
    locations = {r["name"] for r in read("locations.csv")}
    pairs = [frozenset((r["user_a"], r["user_b"])) for r in read("co_present.csv")]
    assert len(pairs) == len(set(pairs)), "CO_PRESENT must be stored once per pair"
    for r in read("co_present.csv"):
        assert r["user_a"] in users and r["user_b"] in users
    for r in read("was_at.csv"):
        assert r["username"] in users and r["location"] in locations
    for r in read("mentioned.csv"):
        assert r["speaker"] in users and r["target"] in users
    for r in read("family.csv"):
        assert r["user_a"] in users and r["user_b"] in users
    for r in read("follows_seed.csv") + read("synthetic_follows.csv"):
        assert r["follower"] in users and r["followed"] in users
        assert r["follower"] != r["followed"]
    for r in read("synthetic_users.csv"):
        assert r["home_location"] in locations


def test_synthetic_follows_start_from_synthetic_users():
    synthetic = {r["username"] for r in read("synthetic_users.csv")}
    assert all(r["follower"] in synthetic for r in read("synthetic_follows.csv"))


# ---------------- signal quality ----------------

@pytest.mark.parametrize("a,b,minimum", [
    ("jon_snow", "samwell_tarly", 70),
    ("arya_stark", "sandor_clegane", 60),
    ("bronn", "tyrion_lannister", 50),
    ("daenerys_targaryen", "jorah_mormont", 140),
])
def test_known_strong_pairs(a, b, minimum):
    assert pair_count(read("co_present.csv"), a, b) >= minimum


def test_top_mentions_make_sense():
    rows = sorted(read("mentioned.csv"), key=lambda r: -int(r["count"]))
    top = {(r["speaker"], r["target"]) for r in rows[:20]}
    assert ("jorah_mormont", "daenerys_targaryen") in top
    assert ("bran_stark", "hodor") in top
    assert ("cersei_lannister", "joffrey_baratheon") in top


def test_no_night_king_false_hits():
    jon = sorted((r for r in read("mentioned.csv") if r["speaker"] == "jon_snow"),
                 key=lambda r: -int(r["count"]))
    assert "the_night_king" not in [r["target"] for r in jon[:3]]


def test_extras_do_not_capture_common_words():
    # e.g. "Citadel" must not become a mention of "Citadel Maester"
    targets = {r["target"] for r in read("mentioned.csv")}
    assert "citadel_maester" not in targets and "dornish_lord" not in targets


def test_first_season_is_valid():
    for r in read("co_present.csv") + read("was_at.csv"):
        assert 1 <= int(r["first_season"]) <= 8
