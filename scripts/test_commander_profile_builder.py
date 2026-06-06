#!/usr/bin/env python3
"""
test_commander_profile_builder.py
Script de test pour inspecter le profil généré par le nouveau module.

Usage :
    python scripts/test_commander_profile_builder.py --commander "Shadowfax, Lord of Horses"
    python scripts/test_commander_profile_builder.py --commander "Galadriel, Light of Valinor"
    python scripts/test_commander_profile_builder.py --commander "Muldrotha, the Gravetide"
    python scripts/test_commander_profile_builder.py --commander "Shadowfax, Lord of Horses" --rebuild
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Forcer UTF-8 sur la console Windows pour les accents et caractères spéciaux
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))


def _sep(char: str = "-", width: int = 70) -> None:
    print(char * width)


def _section(title: str) -> None:
    print()
    _sep("=")
    print(f"  {title}")
    _sep("=")


def _row(label: str, value: object, indent: int = 2) -> None:
    pad = " " * indent
    label_str = f"{label:<35}"
    print(f"{pad}{label_str} {value}")


def print_profile_report(profile: dict) -> None:
    """Affiche un rapport lisible du profil."""
    _section("PROFIL COMMANDANT")

    # ── En-tête ───────────────────────────────────────────────────────────────
    cmd_info = profile.get("commander", {})
    if isinstance(cmd_info, dict):
        _row("Commandant :", cmd_info.get("name", profile.get("commander_name", "?")))
        _row("Identite couleur :", cmd_info.get("color_identity", []))
        _row("Mana value :", cmd_info.get("mana_value", "?"))
        _row("Type :", cmd_info.get("type_line", "?"))
        oracle = cmd_info.get("oracle_text", "")
        if oracle:
            print(f"  {'Oracle text':<35} {oracle[:100]}{'...' if len(oracle) > 100 else ''}")
    else:
        _row("Commandant :", profile.get("commander_name", str(cmd_info)))

    _row("Source :", profile.get("source", "?"))
    _row("Schema version :", profile.get("schema_version", "legacy"))
    _row("Builder version :", profile.get("builder_version", "legacy"))

    # ── Stratégie ─────────────────────────────────────────────────────────────
    _section("STRATEGIE DETECTEE")
    _row("Strategie primaire :", profile.get("primary_strategy", "?"))
    _row("Confiance :", f"{profile.get('strategy_confidence', '?'):.3f}" if isinstance(profile.get('strategy_confidence'), float) else profile.get("strategy_confidence", "?"))
    secondary = profile.get("secondary_strategies", [])
    _row("Strategies secondaires :", ", ".join(secondary) if secondary else "(aucune)")

    # ── Hypothèses ────────────────────────────────────────────────────────────
    hypotheses = profile.get("strategy_hypotheses", [])
    if hypotheses:
        print()
        print("  Hypotheses d'archetypes :")
        for h in hypotheses[:6]:
            bar = "#" * int(h.get("confidence", 0) * 20)
            print(f"    {h['strategy']:<30} conf={h.get('confidence', 0):.3f}  [{bar:<20}]  oracle={h.get('oracle_score', 0):.2f}  decklist={h.get('decklist_score', 0):.2f}")

    # ── Rôles ─────────────────────────────────────────────────────────────────
    _section("ROLES")
    wanted = profile.get("wanted_roles", [])
    avoided = profile.get("avoided_roles", [])
    _row("Roles voulus :", ", ".join(wanted[:8]) if wanted else "(aucun)")
    if len(wanted) > 8:
        print(f"{'':37} {', '.join(wanted[8:])}")
    _row("Roles evites :", ", ".join(avoided) if avoided else "(aucun)")

    # ── Types préférés & MV ───────────────────────────────────────────────────
    _section("TYPES ET COURBE")
    _row("Types preferes :", ", ".join(profile.get("preferred_card_types", [])))
    _row("Max mana value :", profile.get("max_preferred_mana_value", "?"))
    curve = profile.get("mana_curve_preference", {})
    if curve:
        _row("Courbe (low/ideal_min/ideal_max/high) :", f"{curve.get('low')}/{curve.get('ideal_min')}/{curve.get('ideal_max')}/{curve.get('high')}")

    # ── Contraintes deckbuilding ───────────────────────────────────────────────
    constraints = profile.get("deckbuilding_constraints", [])
    if constraints:
        _section("CONTRAINTES DECKBUILDING")
        for c in constraints:
            imp = c.get("importance", 0)
            bar = "#" * int(imp * 10)
            print(f"  [{bar:<10}] imp={imp:.1f}  {c.get('constraint', '?')}")
            print(f"{'':15}{c.get('description', '')}")
    else:
        print()
        _row("Contraintes deckbuilding :", "(aucune detectee)")

    # ── Wants ─────────────────────────────────────────────────────────────────
    wants = profile.get("wants", [])
    if wants:
        print()
        print("  Ce que ce deck cherche :")
        for w in wants:
            print(f"    - {w}")

    # ── Evidence ──────────────────────────────────────────────────────────────
    evidence = profile.get("evidence", {})
    if evidence:
        _section("EVIDENCE")
        oracle_sigs = evidence.get("oracle_signals", [])
        decklist_sigs = evidence.get("decklist_signals", [])
        archetype_sigs = evidence.get("archetype_signals", [])
        warnings = evidence.get("warnings", [])

        if oracle_sigs:
            print(f"  Signaux Oracle ({len(oracle_sigs)}) :")
            for s in oracle_sigs[:15]:
                print(f"    - {s}")
            if len(oracle_sigs) > 15:
                print(f"    ... et {len(oracle_sigs) - 15} autres")

        if decklist_sigs:
            print(f"\n  Signaux decklists :")
            for s in decklist_sigs:
                print(f"    - {s}")

        if archetype_sigs:
            print(f"\n  Signaux archetypes :")
            for s in archetype_sigs:
                print(f"    - {s}")

        if warnings:
            print(f"\n  AVERTISSEMENTS :")
            for w in warnings:
                print(f"    !! {w}")

    _sep()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Teste le CommanderProfileBuilder sur un commandant"
    )
    parser.add_argument("--commander", required=True, help="Nom du commandant")
    parser.add_argument("--rebuild", action="store_true", help="Forcer la reconstruction du profil")
    args = parser.parse_args()

    # ── Charger les variables d'environnement ─────────────────────────────────
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")

    # ── Connexion DB ──────────────────────────────────────────────────────────
    import os
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("[ERREUR] DATABASE_URL absent du .env")
        sys.exit(1)

    try:
        from sqlalchemy import create_engine, text
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"[DB] Connexion OK")
    except Exception as exc:
        print(f"[ERREUR] Connexion DB impossible : {exc}")
        sys.exit(1)

    # ── Chercher le commandant en DB ──────────────────────────────────────────
    sql = """
        SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value,
               type_line, oracle_text, power, toughness, colors, color_identity,
               keywords, legal_commander, edhrec_rank
        FROM cards WHERE name = :n LIMIT 1
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"n": args.commander}).fetchone()
        if not row:
            # Recherche approchée
            row = conn.execute(
                text("SELECT id, oracle_id, name, normalized_name, mana_cost, mana_value, type_line, oracle_text, power, toughness, colors, color_identity, keywords, legal_commander, edhrec_rank FROM cards WHERE name ILIKE :n LIMIT 1"),
                {"n": f"%{args.commander}%"}
            ).fetchone()

    if not row:
        print(f"[ERREUR] Commandant '{args.commander}' introuvable en base.")
        sys.exit(1)

    commander_card = dict(row._mapping)
    print(f"[DB] Commandant trouve : {commander_card['name']}")

    # ── Générer le profil ─────────────────────────────────────────────────────
    from src.recommendations.commander_profile.profile_builder import load_or_create_commander_profile

    print()
    profile = load_or_create_commander_profile(
        commander_card,
        force_rebuild=args.rebuild,
    )

    # ── Afficher le rapport ───────────────────────────────────────────────────
    print_profile_report(profile)


if __name__ == "__main__":
    main()
