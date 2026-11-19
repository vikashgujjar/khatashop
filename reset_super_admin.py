"""Set (or reset) a super admin's email and password.

Run it on the server from the project folder:

    python3 reset_super_admin.py superadmin@khatashop.com

It asks for the new password twice (nothing is shown while you type). Then:
  - if a super admin with that email exists, its password is replaced and it is switched on;
  - else, if there is exactly one super admin, that account gets this email and password
    after you confirm (fixes a mistyped email or a broken password hash);
  - else a new super admin is created.
Finally it checks that signing in with the email and password works.

Options:  --id N        update super admin N (also changes its email)
          --name "X"    name for a new account
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app import create_app, db
from app.models import super_admin


def ask_password() -> str:
    while True:
        first = getpass.getpass("New password: ")
        if len(first) < super_admin.MIN_PASSWORD:
            print(f"Use at least {super_admin.MIN_PASSWORD} characters.")
            continue
        if getpass.getpass("Type it again: ") != first:
            print("The two passwords do not match. Try again.")
            continue
        return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Set or reset a super admin's email and password.")
    parser.add_argument("email", help="email to sign in with")
    parser.add_argument("--id", type=int, help="id of the super admin to update")
    parser.add_argument("--name", default="Super Admin", help="name for a new account")
    args = parser.parse_args()

    email = args.email.strip().lower()
    if not super_admin.EMAIL_RE.match(email):
        sys.exit(f"Not a valid email address: {email}")
    password = ask_password()

    app = create_app()
    with app.app_context():
        super_admin.ensure_table()
        hashed = super_admin.hash_password(password)
        rows = db.fetch_all("SELECT id, email FROM ks_super_admin ORDER BY id")
        by_email = next((r for r in rows if (r["email"] or "").strip().lower() == email), None)

        if args.id:
            target = next((r for r in rows if r["id"] == args.id), None)
            if not target:
                sys.exit(f"No super admin with id {args.id}.")
            if by_email and by_email["id"] != args.id:
                sys.exit(f"{email} already belongs to super admin {by_email['id']}.")
        elif by_email:
            target = by_email
        elif len(rows) == 1:
            target = rows[0]
        else:
            target = None

        if target and (target["email"] or "").strip().lower() != email:
            answer = input(f"Change super admin {target['id']} ({target['email']}) to {email}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit("Nothing changed.")

        if target:
            db.execute("UPDATE ks_super_admin SET email = %s, password = %s, active = 1 WHERE id = %s",
                       (email, hashed, target["id"]))
            old = target["email"]
            changed = f" (email was {old})" if (old or "").strip().lower() != email else ""
            print(f"Super admin {target['id']} updated{changed}.")
        else:
            new_id = super_admin.create(args.name, email, password)
            print(f"Super admin {new_id} created.")

        if super_admin.authenticate(email, password):
            print(f"Sign-in check: OK. Sign in with {email} and the password you just typed.")
        else:
            sys.exit("Sign-in check FAILED. Please send this output.")


if __name__ == "__main__":
    main()
