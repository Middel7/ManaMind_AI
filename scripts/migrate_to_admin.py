#!/usr/bin/env python3
"""
migrate_to_admin.py

Crée le compte admin et migre toutes les données existantes vers ce compte :
  - user_collection (ajoute user_id)
  - moxfield_decks.json → user_moxfield_decks
  - data/My decks/*.txt → user_deck_cards

Usage :
    uv run python scripts/migrate_to_admin.py --email admin@manamind.app --password MonMotDePasse

Options :
    --email      Email du compte admin
    --password   Mot de passe (min 8 caractères)
    --reset      Supprime le compte admin existant avant de recréer (DANGER)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
import os

def get_conn():
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(url)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("ERREUR : mot de passe trop court (min 8 caractères)")
        sys.exit(1)

    # Import après chargement .env
    import bcrypt as _bcrypt
    def _hash(plain: str) -> str:
        return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")

    conn = get_conn()

    with conn.cursor() as cur:
        # ── 1. Créer ou récupérer le compte admin ─────────────────
        if args.reset:
            cur.execute("DELETE FROM users WHERE email = %s", (args.email,))
            print(f"  Compte existant supprimé.")

        cur.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)", (args.email,))
        existing = cur.fetchone()

        if existing:
            admin_id = existing[0]
            print(f"  Compte admin existant trouvé (id={admin_id}), mise à jour du mot de passe.")
            pw_hash = _hash(args.password)
            cur.execute("UPDATE users SET password_hash = %s, role = 'admin' WHERE id = %s", (pw_hash, admin_id))
        else:
            pw_hash = _hash(args.password)
            cur.execute("""
                INSERT INTO users (email, password_hash, display_name, role)
                VALUES (%s, %s, %s, 'admin')
                RETURNING id
            """, (args.email, pw_hash, "Admin"))
            admin_id = cur.fetchone()[0]
            print(f"  Compte admin créé (id={admin_id}) : {args.email}")

        # ── 2. Migrer user_collection ─────────────────────────────
        cur.execute("SELECT COUNT(*) FROM user_collection WHERE user_id IS NULL")
        orphan_count = cur.fetchone()[0]
        if orphan_count > 0:
            cur.execute("UPDATE user_collection SET user_id = %s WHERE user_id IS NULL", (admin_id,))
            print(f"  {orphan_count} cartes de collection migrées vers le compte admin.")
        else:
            print("  user_collection : rien à migrer.")

        # ── 3. Migrer moxfield_decks.json → user_moxfield_decks ──
        mox_file = ROOT / "data" / "moxfield_decks.json"
        if mox_file.exists():
            try:
                decks = json.loads(mox_file.read_text(encoding="utf-8"))
                migrated = 0
                for d in decks:
                    deck_id  = d.get("deck_id", "")
                    url      = d.get("url") or d.get("moxfield_url") or ""
                    commander = d.get("commander", "")
                    name     = d.get("name", "")
                    if not deck_id:
                        continue
                    cur.execute("""
                        INSERT INTO user_moxfield_decks (user_id, deck_id, moxfield_url, commander, name)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, deck_id) DO NOTHING
                    """, (admin_id, deck_id, url, commander, name))
                    migrated += 1
                print(f"  {migrated} decks Moxfield migrés.")
            except Exception as e:
                print(f"  AVERTISSEMENT moxfield_decks.json : {e}")
        else:
            print("  moxfield_decks.json introuvable, pas de migration decks Moxfield.")

        # ── 4. Migrer data/My decks/*.txt → user_deck_cards ──────
        decks_dir = ROOT / "data" / "My decks"
        if decks_dir.exists():
            txt_files = list(decks_dir.glob("*.txt"))
            card_count = 0
            for txt_file in txt_files:
                # Lire le fichier et récupérer le commandant depuis user_moxfield_decks
                # Format : "1 Nom de la carte"
                slug = txt_file.stem  # ex: "teysa_karlov"
                # Essayer de retrouver le commandant depuis le JSON
                commander = None
                if mox_file.exists():
                    try:
                        decks_json = json.loads(mox_file.read_text(encoding="utf-8"))
                        safe_slug = re.sub(r"[^a-z0-9]", "_", "".join(c for c in slug))
                        for d in decks_json:
                            cmd = d.get("commander", "")
                            cmd_slug = re.sub(r"[^a-z0-9]", "_", cmd.lower())
                            if cmd_slug == slug or safe_slug in cmd_slug or cmd_slug in safe_slug:
                                commander = cmd
                                break
                        if not commander:
                            # Fallback : utiliser le slug reformaté
                            commander = slug.replace("_", " ").title()
                    except Exception:
                        commander = slug.replace("_", " ").title()
                else:
                    commander = slug.replace("_", " ").title()

                try:
                    content = txt_file.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        line = line.strip()
                        m = re.match(r"^(\d+)\s+(.+)$", line)
                        if not m:
                            continue
                        qty  = int(m.group(1))
                        name = m.group(2).strip()
                        cur.execute("""
                            INSERT INTO user_deck_cards (user_id, commander, card_name, quantity)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (user_id, commander, card_name) DO NOTHING
                        """, (admin_id, commander, name, qty))
                        card_count += 1
                except Exception as e:
                    print(f"  AVERTISSEMENT {txt_file.name} : {e}")

            print(f"  {card_count} cartes de decks migrées depuis {len(txt_files)} fichiers .txt.")
        else:
            print("  data/My decks/ introuvable, pas de migration cartes de decks.")

        conn.commit()

    conn.close()
    print(f"\nOK Migration terminee. Connecte-toi avec : {args.email}")


if __name__ == "__main__":
    main()
