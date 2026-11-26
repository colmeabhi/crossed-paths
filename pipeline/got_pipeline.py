"""
ENCORE data pipeline
Builds Neo4j-ready CSVs from Jeffrey Lancaster's Game of Thrones dataset.

Source data: https://github.com/jeffreylancaster/game-of-thrones  (folder: data/)
Usage:       python pipeline/got_pipeline.py [path-to-repo/data] [output-folder]
Defaults:    data/raw/game-of-thrones/data  ->  data/csv

Outputs:
  users.csv          one row per character kept as a user
  locations.csv      one row per location
  co_present.csv     pairs of users who shared scenes      (signal 1: same scene)
  was_at.csv         user -> location visits                (signal 2: same place)
  mentioned.csv      speaker -> named character            (signal 3: name mentions)
  family.csv         parent / sibling / spouse links        (bonus profile data)
  follows_seed.csv   starting FOLLOWS edges built from strong ties
  synthetic_*.csv    generated users / follows / home regions (see synthetic.py) to reach 1,000+ nodes
"""

import csv
import json
import os
import re
import sys

import synthetic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(ROOT, "data", "raw", "game-of-thrones", "data")
DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "csv")

# ---------------- settings (tune these) ----------------
MIN_SCENES = 1         # keep characters with at least this many scenes (1 = every character, 577 users)
ALIAS_MIN_SCENES = 10  # only characters with this many scenes get an automatic first-word alias
                       # (stops extras like "Citadel Maester" or "Young Cersei" hijacking names)
STRONG_TIE = 20        # shared scenes needed for a mutual seed follow
WEAK_TIE = 5           # shared scenes needed for a one-way seed follow (minor -> major)
COMMON_WORD_LIMIT = 20 # alias skipped if the word appears in lowercase this often (e.g. "will", "night")

TITLES = {"Ser", "Lord", "Lady", "King", "Queen", "Maester", "Grand", "Prince", "Princess",
          "High", "Old", "Young", "Little", "The", "Brother", "Septa", "Septon"}

# hand-checked nicknames: word said in the script -> character name
NICKNAMES = {
    "Ned": "Eddard Stark",
    "Jon": "Jon Snow",
    "Dany": "Daenerys Targaryen",
    "Khaleesi": "Daenerys Targaryen",
    "Sam": "Samwell Tarly",
    "Littlefinger": "Petyr Baelish",
    "Hound": "Sandor Clegane",
    "Imp": "Tyrion Lannister",
    "Mountain": "Gregor Clegane",
    # checked against the scripts: capitalized uses vs. lowercase uses (cap/low)
    "Worm": "Grey Worm",            # 15/3  — "Grey" alone is too common
    "Kingslayer": "Jaime Lannister",  # 57/0
    "Blackfish": "Brynden Tully",   # 24/0
    "Drogo": "Khal Drogo",          # 46/0  — "Khal" is a title
    "Reek": "Theon Greyjoy",        # 50/1  — Ramsay's name for Theon
    "Pod": "Podrick Payne",         # 22/0
    "Joff": "Joffrey Baratheon",    # 3/0
    "Baelish": "Petyr Baelish",     # 65/0  — surname unique among users
    # skipped on purpose: "Snow" (Jon vs Ramsay Snow), "Clegane"/"Bolton"/"Greyjoy" (shared surnames),
    # "Spider" (3/7), "Pie" (7/21), "Night" (183/218)
}


def make_username(name):
    # "Jon Snow" -> "jon_snow"
    username = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return username.strip("_")


u = make_username


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print("wrote", os.path.basename(path), len(rows), "rows")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_raw(data_dir):
    episodes = load_json(os.path.join(data_dir, "episodes.json"))["episodes"]
    script = load_json(os.path.join(data_dir, "script-bag-of-words.json"))
    characters = load_json(os.path.join(data_dir, "characters.json"))["characters"]
    return episodes, script, characters


def scene_characters(scene):
    # a character listed twice in one scene still counts once
    return {c["name"] for c in scene.get("characters", [])}


# Step 1: count scenes per character and pick who becomes a user
def pick_users(episodes):
    scene_count = {}
    for ep in episodes:
        for scene in ep["scenes"]:
            for name in scene_characters(scene):
                scene_count[name] = scene_count.get(name, 0) + 1

    users = {name for name, n in scene_count.items() if n >= MIN_SCENES}

    # two names must never collapse into the same username
    seen = {}
    for name in users:
        u = make_username(name)
        if u in seen:
            raise ValueError(f"username collision: {seen[u]!r} and {name!r} -> {u!r}")
        seen[u] = name
    return users, scene_count


def _bump(table, key, season):
    # table[key] = [count, first_season]; first_season is the minimum seen, whatever the file order
    if key not in table:
        table[key] = [0, season]
    table[key][0] += 1
    table[key][1] = min(table[key][1], season)


# Step 2: walk every scene -> co-presence pairs and location visits
def build_scene_signals(episodes, users):
    pairs = {}        # (a, b) with a < b -> [count, first_season]
    visits = {}       # (name, location) -> [count, first_season]
    location_names = set()
    for ep in episodes:
        season = ep["seasonNum"]
        for scene in ep["scenes"]:
            location = scene["location"]
            location_names.add(location)
            present = sorted(scene_characters(scene) & users)

            for name in present:
                _bump(visits, (name, location), season)
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    _bump(pairs, (present[i], present[j]), season)
    return pairs, visits, location_names


# Step 3: build the alias table (word -> character) for name mentions
def build_aliases(script, users, scene_count):
    lowercase_count = {}
    for ep in script:
        for line in ep["text"]:
            for word in re.findall(r"[a-z]+", line["text"]):
                lowercase_count[word] = lowercase_count.get(word, 0) + 1

    first_word = {}   # word -> list of characters it could mean
    for name in users:
        if scene_count[name] < ALIAS_MIN_SCENES:
            continue
        parts = name.split()
        i = 0
        while i < len(parts) - 1 and parts[i] in TITLES:
            i += 1
        first_word.setdefault(parts[i], []).append(name)

    alias = {}
    for word, names in first_word.items():
        if len(names) != 1 or len(word) <= 2 or word in TITLES:
            continue                                   # ambiguous or too short
        if lowercase_count.get(word.lower(), 0) > COMMON_WORD_LIMIT:
            continue                                   # common English word ("Will", "Night")
        alias[word] = names[0]
    for word, name in NICKNAMES.items():
        if name in users:
            alias[word] = name
    return alias


def lower_lookup(users):
    # match other spellings to scene names, ignoring letter case ("Thoros Of Myr")
    return {name.lower(): name for name in users}


# Step 4: scan each spoken line for names -> MENTIONED edges
def build_mentions(script, users, alias):
    by_lower = lower_lookup(users)
    mentions = {}     # (speaker, target) -> count
    for ep in script:
        for line in ep["text"]:
            speaker = by_lower.get(line["name"].lower())
            if speaker is None:
                continue
            for word in re.findall(r"[A-Z][a-z]+", line["text"]):
                target = alias.get(word)
                if target is not None and target != speaker:
                    key = (speaker, target)
                    mentions[key] = mentions.get(key, 0) + 1
    return mentions


# Step 5: family links + house from characters.json
def build_family(characters, users):
    by_lower = lower_lookup(users)
    family = set()
    house = {}
    for c in characters:
        name = by_lower.get(c["characterName"].lower())
        if name is None:
            continue
        h = c.get("houseName", "")
        if isinstance(h, list):
            h = h[0] if h else ""
        house[name] = h
        for field, rel in [("parents", "CHILD_OF"), ("siblings", "SIBLING_OF"), ("marriedEngaged", "MARRIED_TO")]:
            for raw in c.get(field, []):
                other = by_lower.get(raw.lower())
                if other is not None and other != name:
                    family.add((name, other, rel))
    return family, house


# Step 6: seed FOLLOWS from strong ties so "mutual friends" works from day one
def seed_follows(pairs):
    degree = {}
    for a, b in pairs:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1

    follows = set()
    for (a, b), (count, season) in pairs.items():
        if count >= STRONG_TIE:
            follows.add((a, b, season))
            follows.add((b, a, season))
        elif count >= WEAK_TIE:
            if degree[a] < degree[b]:
                follows.add((a, b, season))            # minor character follows major one
            else:
                follows.add((b, a, season))
    return follows


# Step 7: write everything out
def write_all(out_dir, users, scene_count, house, location_names, pairs, visits, mentions, family, follows):
    os.makedirs(out_dir, exist_ok=True)

    top_location = {}
    for (name, location), (count, _season) in visits.items():
        if name not in top_location or count > top_location[name][1]:
            top_location[name] = (location, count)

    user_rows = []
    for name in sorted(users):
        h = house.get(name, "")
        place = top_location.get(name, ("", 0))[0]
        bio_parts = []
        if h:
            bio_parts.append("House " + h)
        bio_parts.append(f"{scene_count[name]} scene{'' if scene_count[name] == 1 else 's'}")
        if place:
            bio_parts.append("most often at " + place)
        # email is SYNTHETIC: the dataset has none; .example is a reserved, never-real domain
        user_rows.append([u(name), name, u(name) + "@westeros.example", h, scene_count[name], place,
                          " · ".join(bio_parts)])

    write_csv(os.path.join(out_dir, "users.csv"),
              ["username", "name", "email", "house", "scene_count", "top_location", "bio"], user_rows)
    write_csv(os.path.join(out_dir, "locations.csv"), ["name"],
              [[loc] for loc in sorted(location_names)])
    write_csv(os.path.join(out_dir, "co_present.csv"), ["user_a", "user_b", "count", "first_season"],
              [[u(a), u(b), c, s] for (a, b), (c, s) in sorted(pairs.items())])
    write_csv(os.path.join(out_dir, "was_at.csv"), ["username", "location", "count", "first_season"],
              [[u(n), loc, c, s] for (n, loc), (c, s) in sorted(visits.items())])
    write_csv(os.path.join(out_dir, "mentioned.csv"), ["speaker", "target", "count"],
              [[u(a), u(b), c] for (a, b), c in sorted(mentions.items())])
    write_csv(os.path.join(out_dir, "family.csv"), ["user_a", "user_b", "type"],
              [[u(a), u(b), rel] for a, b, rel in sorted(family)])
    write_csv(os.path.join(out_dir, "follows_seed.csv"), ["follower", "followed", "since_season"],
              [[u(a), u(b), s] for a, b, s in sorted(follows)])


def scene_count_by_username(users, scene_count):
    return {u(n): scene_count[n] for n in users}


def follows_by_username(follows):
    return {(u(a), u(b)) for a, b, _season in follows}


def run(data_dir=DEFAULT_DATA_DIR, out_dir=DEFAULT_OUT_DIR):
    episodes, script, characters = load_raw(data_dir)
    users, scene_count = pick_users(episodes)
    pairs, visits, location_names = build_scene_signals(episodes, users)
    alias = build_aliases(script, users, scene_count)
    mentions = build_mentions(script, users, alias)
    family, house = build_family(characters, users)
    follows = seed_follows(pairs)
    write_all(out_dir, users, scene_count, house, location_names, pairs, visits, mentions, family, follows)
    synthetic.generate(out_dir, [u(n) for n in users], scene_count_by_username(users, scene_count),
                       sorted(location_names), follows_by_username(follows))

    # Step 8: quick sanity checks (eyeball these every run)
    print("\nusers:", len(users), "| aliases:", len(alias))
    print("top co-presence pairs:")
    for key, value in sorted(pairs.items(), key=lambda kv: -kv[1][0])[:5]:
        print("   ", key, value[0])
    print("top mentions:")
    for key, value in sorted(mentions.items(), key=lambda kv: -kv[1])[:8]:
        print("   ", key, value)


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args[0] if len(args) > 0 else DEFAULT_DATA_DIR,
        args[1] if len(args) > 1 else DEFAULT_OUT_DIR)
