"""
ENCORE loader: data/csv/*.csv -> Neo4j.

Usage:  python pipeline/load_db.py [--reset] [csv-folder]

Re-runnable: every write is a MERGE, so running twice gives the same graph.
--reset deletes only the seeded data (character users + locations) first,
so real users who registered through the app survive a rebuild.
Connection settings come from .env (see .env.example).
"""

import argparse
import csv
import os

import bcrypt
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV_DIR = os.path.join(ROOT, "data", "csv")
BATCH_SIZE = 500

CONSTRAINTS = [
    "CREATE CONSTRAINT user_username IF NOT EXISTS FOR (u:User) REQUIRE u.username IS UNIQUE",
    "CREATE CONSTRAINT location_name IF NOT EXISTS FOR (l:Location) REQUIRE l.name IS UNIQUE",
]

RESET = [
    "MATCH (u:User {isCharacter: true}) DETACH DELETE u",
    "MATCH (l:Location) DETACH DELETE l",
]

# (csv file, Cypher, integer columns) — loaded in this order
STEPS = [
    ("locations.csv",
     "UNWIND $rows AS r MERGE (:Location {name: r.name})",
     []),
    ("users.csv",
     """UNWIND $rows AS r
        MERGE (u:User {username: r.username})
        SET u.name = r.name,
            u.house = r.house,
            u.sceneCount = r.scene_count,
            u.topLocation = r.top_location,
            u.bio = r.bio,
            u.isCharacter = true,
            u.passwordHash = $password_hash,
            u.createdAt = coalesce(u.createdAt, datetime())""",
     ["scene_count"]),
    ("co_present.csv",
     """UNWIND $rows AS r
        MATCH (a:User {username: r.user_a}), (b:User {username: r.user_b})
        MERGE (a)-[c:CO_PRESENT]->(b)
        SET c.count = r.count, c.firstSeason = r.first_season""",
     ["count", "first_season"]),
    ("was_at.csv",
     """UNWIND $rows AS r
        MATCH (u:User {username: r.username}), (l:Location {name: r.location})
        MERGE (u)-[w:WAS_AT]->(l)
        SET w.count = r.count, w.firstSeason = r.first_season""",
     ["count", "first_season"]),
    ("mentioned.csv",
     """UNWIND $rows AS r
        MATCH (a:User {username: r.speaker}), (b:User {username: r.target})
        MERGE (a)-[m:MENTIONED]->(b)
        SET m.count = r.count""",
     ["count"]),
    ("family.csv",
     """UNWIND $rows AS r
        MATCH (a:User {username: r.user_a}), (b:User {username: r.user_b})
        MERGE (a)-[:FAMILY {type: r.type}]->(b)""",
     []),
    ("follows_seed.csv",
     """UNWIND $rows AS r
        MATCH (a:User {username: r.follower}), (b:User {username: r.followed})
        MERGE (a)-[f:FOLLOWS]->(b)
        ON CREATE SET f.sinceSeason = r.since_season""",
     ["since_season"]),
]


def read_rows(path, int_columns):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for col in int_columns:
            r[col] = int(r[col])
    return rows


def batches(rows, size=BATCH_SIZE):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def print_counts(session):
    print("\nnodes:")
    for rec in session.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label"):
        print(f"    {rec['label']:<12} {rec['n']}")
    print("relationships:")
    for rec in session.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type"):
        print(f"    {rec['type']:<12} {rec['n']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_dir", nargs="?", default=DEFAULT_CSV_DIR)
    parser.add_argument("--reset", action="store_true", help="delete seeded character data before loading")
    args = parser.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    uri = os.environ["NEO4J_URI"]
    auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    demo_password = os.environ.get("DEMO_PASSWORD", "winteriscoming")
    # one hash shared by every character: bcrypt is slow on purpose, 157 hashes would take seconds
    password_hash = bcrypt.hashpw(demo_password.encode(), bcrypt.gensalt()).decode()

    with GraphDatabase.driver(uri, auth=auth) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for q in CONSTRAINTS:
                session.run(q).consume()
            if args.reset:
                for q in RESET:
                    session.run(q).consume()
                print("reset: removed seeded characters and locations")

            for filename, query, int_columns in STEPS:
                rows = read_rows(os.path.join(args.csv_dir, filename), int_columns)
                for chunk in batches(rows):
                    session.execute_write(
                        lambda tx, c=chunk: tx.run(query, rows=c, password_hash=password_hash).consume())
                print(f"loaded {filename:<18} {len(rows)} rows")

            print_counts(session)


if __name__ == "__main__":
    main()
