"""Data sanity checks from the spec (§13). Run after `python pipeline/got_pipeline.py`."""

import csv
import os

import pytest

CSV_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "csv")


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


def test_sizes_in_expected_range():
    assert 140 <= len(read("users.csv")) <= 175
    assert len(read("locations.csv")) == 26
    assert 1800 <= len(read("co_present.csv")) <= 2300
    assert 400 <= len(read("was_at.csv")) <= 600
    assert 1000 <= len(read("mentioned.csv")) <= 1500


def test_usernames_unique():
    names = [r["username"] for r in read("users.csv")]
    assert len(names) == len(set(names))


def test_every_edge_endpoint_is_a_user():
    users = {r["username"] for r in read("users.csv")}
    locations = {r["name"] for r in read("locations.csv")}
    for r in read("co_present.csv"):
        assert r["user_a"] in users and r["user_b"] in users
        assert r["user_a"] < r["user_b"], "CO_PRESENT must be stored once, ordered"
    for r in read("was_at.csv"):
        assert r["username"] in users and r["location"] in locations
    for r in read("mentioned.csv"):
        assert r["speaker"] in users and r["target"] in users
    for r in read("family.csv"):
        assert r["user_a"] in users and r["user_b"] in users
    for r in read("follows_seed.csv"):
        assert r["follower"] in users and r["followed"] in users


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


def test_no_night_king_false_hits():
    jon = sorted((r for r in read("mentioned.csv") if r["speaker"] == "jon_snow"),
                 key=lambda r: -int(r["count"]))
    assert "the_night_king" not in [r["target"] for r in jon[:3]]


def test_first_season_is_valid():
    for r in read("co_present.csv") + read("was_at.csv"):
        assert 1 <= int(r["first_season"]) <= 8
