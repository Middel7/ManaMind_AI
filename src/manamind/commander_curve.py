"""commander_curve.py — La courbe de mana des decks qui jouent un commandant.

Un cout moyen ne se juge pas dans l'absolu : 2,8 est eleve pour un deck qui
deroule des sorts a un mana, bas pour un deck de gros sorts. Le point de
comparaison qui a du sens est l'ensemble des decks publics jouant le meme
commandant.

Le calcul ne retient que les sorts : les terrains, qui valent zero, tireraient
toutes les moyennes vers le bas, et le commandant lui-meme ne fait pas partie
des cartes que l'on choisit — c'est la contrainte, pas le choix. La courbe
affichee dans les pages suit la meme regle, faute de quoi la comparaison
porterait sur deux mesures differentes.

Chaque deck compte pour un : les parts sont moyennees deck par deck, et non
calculees sur le tas de toutes les cartes, ou les listes les plus longues
pesseraient le plus lourd.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from manamind.commanders import commander_keys
from manamind.db.engine import SessionLocal

log = logging.getLogger(__name__)

# Nombre de barres de la courbe : couts 0 a 6, puis 7 et plus.
BUCKETS = 8

# Les noms de cartes sont d'abord reduits a leur liste distincte : normaliser
# des deux cotes d'une jointure ligne a ligne rend la comparaison quadratique.
_REFERENCE_SQL = """
WITH deck_card AS (
    SELECT dc.deck_id, dc.card_name, dc.quantity
    FROM deck_cards dc
    WHERE dc.commander = :cmd AND dc.is_commander = false
      -- Quelques decks publics listent leur commandant avec les autres cartes
      -- plutot que dans la zone de commandement : le drapeau ne suffit pas a
      -- l'ecarter, et son cout entrerait dans la moyenne des sorts choisis.
      AND lower(btrim(dc.card_name)) <> ALL(CAST(:cmd_keys AS TEXT[]))
),
names AS (SELECT DISTINCT card_name FROM deck_card),
resolved AS (
    SELECT n.card_name, sc.mana_value, sc.type_line
    FROM names n
    LEFT JOIN LATERAL (
        SELECT c.mana_value, c.type_line
        FROM scryfall_cards c
        WHERE c.normalized_name = mm_normalize_name(n.card_name)
        ORDER BY (c.type_line NOT ILIKE '%%Token%%') DESC, c.id
        LIMIT 1
    ) sc ON TRUE
),
spell AS (
    SELECT d.deck_id, d.quantity,
           LEAST(:last_bucket, FLOOR(r.mana_value))::int AS bucket,
           r.mana_value
    FROM deck_card d
    JOIN resolved r ON r.card_name = d.card_name
    WHERE r.mana_value IS NOT NULL AND r.type_line NOT ILIKE '%%Land%%'
),
per_deck AS (
    SELECT deck_id, SUM(mana_value * quantity) AS sum_mv, SUM(quantity) AS n
    FROM spell
    GROUP BY deck_id
    HAVING SUM(quantity) > 0
),
per_bucket AS (
    SELECT s.deck_id, s.bucket, SUM(s.quantity)::numeric / p.n AS share
    FROM spell s
    JOIN per_deck p ON p.deck_id = s.deck_id
    GROUP BY s.deck_id, s.bucket, p.n
),
buckets AS (
    SELECT g.bucket, AVG(COALESCE(pb.share, 0)) * 100 AS pct
    FROM per_deck d
    CROSS JOIN generate_series(0, :last_bucket) AS g(bucket)
    LEFT JOIN per_bucket pb ON pb.deck_id = d.deck_id AND pb.bucket = g.bucket
    GROUP BY g.bucket
)
SELECT (SELECT ROUND(AVG(sum_mv / n)::numeric, 3) FROM per_deck) AS avg_mana_value,
       (SELECT COUNT(*) FROM per_deck) AS decks,
       ARRAY(SELECT ROUND(pct::numeric, 3) FROM buckets ORDER BY bucket) AS curve
"""

_READ_SQL = """
    SELECT commander, decks, avg_mana_value, curve
    FROM deck_stat_commander_curve
    WHERE commander = :cmd
"""

_WRITE_SQL = """
    INSERT INTO deck_stat_commander_curve
        (commander, decks, avg_mana_value, curve, computed_at)
    VALUES (:cmd, :decks, :avg, CAST(:curve AS numeric[]), now())
    ON CONFLICT (commander) DO UPDATE SET
        decks          = EXCLUDED.decks,
        avg_mana_value = EXCLUDED.avg_mana_value,
        curve          = EXCLUDED.curve,
        computed_at    = now()
"""

_DELETE_SQL = "DELETE FROM deck_stat_commander_curve WHERE commander = :cmd"


def compute(session: Session, commander: str) -> dict | None:
    """Calcule la reference depuis les decks publics, sans rien enregistrer.

    Retourne None quand aucun deck public ne porte ce commandant, ou qu'aucune
    de leurs cartes n'a pu etre reliee a un cout de mana connu.
    """
    row = session.execute(
        text(_REFERENCE_SQL),
        {
            "cmd": commander,
            "last_bucket": BUCKETS - 1,
            "cmd_keys": sorted(commander_keys(commander)),
        },
    ).fetchone()

    if row is None or not row.decks or row.avg_mana_value is None:
        return None

    return {
        "commander": commander,
        "decks": int(row.decks),
        "avg_mana_value": float(row.avg_mana_value),
        "curve": [float(part) for part in (row.curve or [])],
    }


def store(session: Session, commander: str, reference: dict | None) -> None:
    """Enregistre la reference, ou efface celle qui n'a plus lieu d'etre."""
    if reference is None:
        session.execute(text(_DELETE_SQL), {"cmd": commander})
        return

    session.execute(
        text(_WRITE_SQL),
        {
            "cmd": commander,
            "decks": reference["decks"],
            "avg": reference["avg_mana_value"],
            "curve": reference["curve"],
        },
    )


def refresh(session: Session, commander: str) -> dict | None:
    """Recalcule et enregistre la reference d'un commandant."""
    reference = compute(session, commander)
    store(session, commander, reference)
    return reference


def reference_for(commander: str | None) -> dict | None:
    """La reference d'un commandant, calculee a la premiere demande.

    Les commandants sont trop nombreux pour que le premier calcul complet soit
    un prealable a l'affichage : la table se remplit a l'usage, et le scrape la
    reecrit ensuite pour les commandants qu'il touche.
    """
    name = (commander or "").strip()
    if not name:
        return None

    with SessionLocal() as session:
        row = session.execute(text(_READ_SQL), {"cmd": name}).fetchone()
        if row is not None:
            return {
                "commander": row.commander,
                "decks": int(row.decks),
                "avg_mana_value": float(row.avg_mana_value),
                "curve": [float(part) for part in (row.curve or [])],
            }

        reference = compute(session, name)
        if reference is None:
            return None

        # Un echec d'ecriture ne doit pas priver la page de sa comparaison :
        # elle est juste recalculee au prochain affichage.
        try:
            store(session, name, reference)
            session.commit()
        except Exception:  # pragma: no cover - depend de l'etat de la base
            session.rollback()
            log.warning("Courbe de reference non enregistree pour %s", name, exc_info=True)

        return reference
