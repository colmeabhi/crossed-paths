"""
Synthetic data for ENCORE: everything in this file is GENERATED, not from the public dataset.

The course requires at least 1,000 nodes. The Game of Thrones dataset gives 577 characters
and 26 locations, so we add synthetic "smallfolk" users who use the app alongside the characters.

Written to separate files so the report can clearly tell real data from generated data:
  synthetic_users.csv    username, name, email, bio, home_location
  synthetic_follows.csv  follower, followed

Generated with a fixed seed, so every run produces exactly the same data.
"""

import os
import random

from faker import Faker

N_USERS = 450              # 577 characters + 450 synthetic + 26 locations = 1,053 nodes
SEED = 42                  # same seed -> same users and follows on every run
FOLLOWS_PER_USER = (3, 10) # each synthetic user follows between 3 and 10 people
SHARE_FOLLOWING_CHARACTERS = 0.7  # 70% of their follows go to characters, 30% to other synthetic users


def _unique_username(name, taken):
    base = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
    username, n = base, 2
    while username in taken:
        username, n = f"{base}_{n}", n + 1
    taken.add(username)
    return username


def make_users(character_usernames, locations, rng, fake):
    taken = set(character_usernames)
    users = []
    for _ in range(N_USERS):
        name = fake.name()
        username = _unique_username(name, taken)
        region = rng.choice(locations)
        users.append({
            "username": username,
            "name": name,
            "email": f"{username}@example.com",
            "bio": f"Smallfolk from {region}",
            "home_location": region,
        })
    return users


def make_follows(synthetic_usernames, character_scene_count, rng):
    """Synthetic users follow characters (popular ones more often) and each other.

    Characters are picked with probability proportional to their scene count, so main
    characters gain more followers, like celebrities on a real network.
    """
    characters = sorted(character_scene_count)  # sorted: set/dict order varies between runs
    weights = [character_scene_count[c] for c in characters]
    follows = set()
    for me in synthetic_usernames:
        k = rng.randint(*FOLLOWS_PER_USER)
        chosen = set()
        while len(chosen) < k:
            if rng.random() < SHARE_FOLLOWING_CHARACTERS:
                target = rng.choices(characters, weights=weights)[0]
            else:
                target = rng.choice(synthetic_usernames)
            if target != me:
                chosen.add(target)
        follows.update((me, t) for t in chosen)
    return follows


def generate(out_dir, character_usernames, character_scene_count, locations, existing_follows):
    # imported here to avoid a circular import (got_pipeline imports this module)
    from got_pipeline import write_csv

    rng = random.Random(SEED)
    fake = Faker()
    fake.seed_instance(SEED)

    users = make_users(character_usernames, locations, rng, fake)
    follows = make_follows([x["username"] for x in users], character_scene_count, rng)
    assert not follows & existing_follows  # synthetic follows always start from a synthetic user

    write_csv(os.path.join(out_dir, "synthetic_users.csv"),
              ["username", "name", "email", "bio", "home_location"],
              [[x["username"], x["name"], x["email"], x["bio"], x["home_location"]] for x in users])
    write_csv(os.path.join(out_dir, "synthetic_follows.csv"), ["follower", "followed"], sorted(follows))
