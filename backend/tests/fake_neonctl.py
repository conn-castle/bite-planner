"""Stands in for `neonctl api` in family tests.

Handles the two Neon Auth user calls castle-chef makes, applying them to the
test database's `neon_auth.user` stand-in, and logs each call to
$FAKE_NEONCTL_LOG. Exits 1 when $FAKE_NEONCTL_FAIL is set.
"""

import json
import os
import sys

import psycopg

_, command, path, *options = sys.argv
with open(os.environ["FAKE_NEONCTL_LOG"], "a") as log:
    log.write(json.dumps([command, path, *options]) + "\n")
if os.environ.get("FAKE_NEONCTL_FAIL"):
    print("ERROR: simulated Neon failure", file=sys.stderr)
    sys.exit(1)

method = options[options.index("-X") + 1]
with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
    if method == "POST":
        body = json.loads(options[options.index("-d") + 1])
        (user_id,) = connection.execute(
            'INSERT INTO neon_auth."user" (email, name) VALUES (%s, %s) RETURNING id',
            (body["email"], body["name"]),
        ).fetchone()
        print(json.dumps({"id": str(user_id)}, indent=2))
    elif method == "DELETE":
        connection.execute('DELETE FROM neon_auth."user" WHERE id = %s', (path.rsplit("/", 1)[1],))
