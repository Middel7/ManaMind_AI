"""
commander_swap.py — Quel autre commandant tirerait le meilleur parti de ce deck ?

Prend une decklist existante de l'utilisateur et classe les commandants de la
base de statistiques (deck_stat_commander) selon la valeur en euros des cartes
de la liste qu'ils conserveraient.

Une carte est :
  - « kept »      : son identité couleur est incluse dans celle du commandant
                    ET le commandant la joue dans au moins MIN_INCLUSION % de
                    ses decks ;
  - « color »     : perdue car hors identité couleur du commandant ;
  - « archetype » : jouable en couleur mais trop rare dans ses decks.

Les cartes « neutres » (terrains de base et cartes jouées dans plus de
STAPLE_THRESHOLD % de tous les decks) sont exclues du score : elles sont
conservées quel que soit le commandant et écraseraient le classement.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from manamind.db.engine import SessionLocal

# Seuil (en % de tous les decks) au-delà duquel une carte est considérée
# comme un « staple » neutre, exclu du score.
DEFAULT_STAPLE_THRESHOLD = 40.0

# Taux d'inclusion minimal (en % des decks du commandant) pour qu'une carte
# compte comme conservée : en dessous, le commandant ne la joue qu'à la marge.
DEFAULT_MIN_INCLUSION = 10.0

# Nombre de cartes manquantes (les plus chères) remontées par commandant, et
# taux d'inclusion minimal pour qu'une carte absente mérite d'être achetée :
# au-delà de ce seuil, elle fait partie du socle du commandant.
MISSING_TOP = 14
MISSING_MIN_INCLUSION = 50.0

BASIC_LANDS = {
    "island", "plains", "swamp", "mountain", "forest", "wastes",
    "snow-covered island", "snow-covered plains", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest",
}


# ── Requête 1 : résolution des cartes du deck (identité couleur + prix EUR) ───
_DECK_SQL = """
WITH deck_in AS (
    SELECT *
    FROM unnest(CAST(:names AS TEXT[]), CAST(:qtys AS INTEGER[]))
         AS t(card_lower, qty)
),
exact_match AS (
    -- Match direct sur normalized_name (indexé)
    SELECT DISTINCT ON (d.card_lower) d.card_lower, sc.id AS card_id
    FROM deck_in d
    JOIN scryfall_cards sc ON sc.normalized_name = d.card_lower
    ORDER BY d.card_lower, sc.id
),
front_match AS (
    -- Repli : la decklist ne cite que la face avant d'une carte recto-verso
    SELECT DISTINCT ON (d.card_lower) d.card_lower, sc.id AS card_id
    FROM deck_in d
    JOIN scryfall_cards sc
      ON split_part(sc.normalized_name, ' // ', 1) = d.card_lower
    WHERE d.card_lower NOT IN (SELECT card_lower FROM exact_match)
    ORDER BY d.card_lower, sc.id
),
resolved AS (
    SELECT * FROM exact_match
    UNION ALL
    SELECT * FROM front_match
),
deck_cards AS (
    SELECT d.card_lower, d.qty, sc.id AS card_id, sc.name AS display_name,
           COALESCE(sc.color_identity, CAST('{}' AS VARCHAR[])) AS ci,
           sc.type_line
    FROM deck_in d
    LEFT JOIN resolved r ON r.card_lower = d.card_lower
    LEFT JOIN scryfall_cards sc ON sc.id = r.card_id
),
prices AS (
    -- Prix EUR non-foil le plus bas parmi les éditions, à la date la plus récente
    SELECT dc.card_lower, MIN(lp.price) AS eur_price
    FROM deck_cards dc
    JOIN scryfall_card_printings pr ON pr.card_id = dc.card_id
    JOIN LATERAL (
        SELECT p.price
        FROM scryfall_card_prices p
        WHERE p.printing_id = pr.id
          AND p.currency = 'eur'
          AND p.price_type = 'regular'
          AND p.price > 0
        ORDER BY p.date DESC
        LIMIT 1
    ) lp ON TRUE
    GROUP BY dc.card_lower
)
SELECT dc.card_lower,
       dc.qty,
       dc.display_name,
       dc.ci,
       dc.type_line,
       (dc.card_id IS NOT NULL) AS is_resolved,
       COALESCE(pz.eur_price, 0) AS price,
       COALESCE(g.global_frequency, 0) AS global_frequency,
       img.image_normal
FROM deck_cards dc
LEFT JOIN prices pz ON pz.card_lower = dc.card_lower
LEFT JOIN deck_stat_global g ON LOWER(BTRIM(g.card_name)) = dc.card_lower
LEFT JOIN LATERAL (
    SELECT p.image_normal
    FROM scryfall_card_printings p
    WHERE p.card_id = dc.card_id
      AND p.image_normal IS NOT NULL
      AND p.lang = 'en'
    ORDER BY (p.set_code NOT ILIKE 'sl%') DESC, p.released_at DESC NULLS LAST, p.id
    LIMIT 1
) img ON TRUE
"""


# ── Requête 2 : classement des commandants candidats ─────────────────────────
_SWAP_SQL = """
WITH deck AS (
    SELECT t.card_lower, t.display_name, t.price,
           CASE WHEN t.ci_str = '' THEN CAST('{}' AS TEXT[])
                ELSE regexp_split_to_array(t.ci_str, '') END AS ci
    FROM unnest(CAST(:names AS TEXT[]), CAST(:displays AS TEXT[]),
                CAST(:prices AS NUMERIC[]), CAST(:cis AS TEXT[]))
         AS t(card_lower, display_name, price, ci_str)
),
stats AS (
    -- Stats des cartes du deck chez tous les commandants qui en jouent au moins une
    SELECT ds.commander,
           LOWER(BTRIM(ds.card_name)) AS card_lower,
           ds.inclusion_rate,
           ds.total_decks
    FROM deck_stat_commander ds
    WHERE LOWER(BTRIM(ds.card_name)) = ANY(CAST(:names AS TEXT[]))
),
candidates AS (
    SELECT commander, MAX(total_decks) AS total_decks
    FROM stats
    WHERE LOWER(BTRIM(commander)) <> LOWER(BTRIM(:current))
    GROUP BY commander
),
cmd_parts AS (
    -- « A & B », « A / B » : partenaires — l'identité couleur est l'union des deux
    SELECT c.commander, LOWER(BTRIM(p)) AS part
    FROM candidates c,
         LATERAL unnest(string_to_array(REPLACE(c.commander, ' & ', ' / '), ' / ')) AS p
),
part_exact AS (
    SELECT DISTINCT ON (cp.commander, cp.part)
           cp.commander, cp.part, sc.color_identity
    FROM cmd_parts cp
    JOIN scryfall_cards sc ON sc.normalized_name = cp.part
    ORDER BY cp.commander, cp.part, sc.id
),
part_front AS (
    -- Repli : deck_stat_commander ne cite que la face avant d'un commandant recto-verso
    SELECT DISTINCT ON (cp.commander, cp.part)
           cp.commander, cp.part, sc.color_identity
    FROM cmd_parts cp
    JOIN scryfall_cards sc
      ON split_part(sc.normalized_name, ' // ', 1) = cp.part
    WHERE NOT EXISTS (
        SELECT 1 FROM part_exact pe
        WHERE pe.commander = cp.commander AND pe.part = cp.part
    )
    ORDER BY cp.commander, cp.part, sc.id
),
part_ci AS (
    SELECT commander, color_identity FROM part_exact
    UNION ALL
    SELECT commander, color_identity FROM part_front
),
cmd_ci AS (
    -- Commandants dont aucune partie du nom n'est résolue → écartés (pas de ligne)
    SELECT pc.commander,
           CAST(COALESCE(array_agg(DISTINCT col) FILTER (WHERE col IS NOT NULL),
                         CAST('{}' AS VARCHAR[])) AS TEXT[]) AS ci
    FROM part_ci pc
    LEFT JOIN LATERAL unnest(pc.color_identity) AS col ON TRUE
    GROUP BY pc.commander
),
per_card AS (
    SELECT cc.commander, d.display_name, d.price,
           COALESCE(st.inclusion_rate, 0) AS inclusion_rate,
           CASE WHEN NOT (d.ci <@ cc.ci) THEN 'color'
                WHEN COALESCE(st.inclusion_rate, 0) < :min_inclusion THEN 'archetype'
                ELSE 'kept' END AS status
    FROM cmd_ci cc
    CROSS JOIN deck d
    LEFT JOIN stats st
           ON st.commander = cc.commander
          AND st.card_lower = d.card_lower
),
agg AS (
    SELECT commander,
           COALESCE(SUM(price) FILTER (WHERE status = 'kept'), 0) AS kept_value,
           COALESCE(SUM(price * inclusion_rate / 100.0)
                    FILTER (WHERE status = 'kept'), 0) AS affinity_value,
           COUNT(*) FILTER (WHERE status = 'kept')      AS kept_count,
           COUNT(*) FILTER (WHERE status = 'color')     AS lost_color_count,
           COUNT(*) FILTER (WHERE status = 'archetype') AS lost_archetype_count
    FROM per_card
    GROUP BY commander
),
best AS (
    SELECT a.*
    FROM agg a
    JOIN cmd_ci cc ON cc.commander = a.commander
    WHERE cardinality(cc.ci) <= :max_colors
    ORDER BY CASE WHEN :sort = 'affinity' THEN a.affinity_value
                  ELSE a.kept_value END DESC,
             a.kept_count DESC
    LIMIT :top
),
cmd_img AS (
    SELECT b.commander, img.image_normal
    FROM best b
    LEFT JOIN LATERAL (
        SELECT p.image_normal
        FROM scryfall_cards sc
        JOIN scryfall_card_printings p ON p.card_id = sc.id
        WHERE sc.normalized_name = LOWER(BTRIM(split_part(
                  REPLACE(b.commander, ' & ', ' / '), ' / ', 1)))
          AND p.image_normal IS NOT NULL
          AND p.lang = 'en'
        ORDER BY (p.set_code NOT ILIKE 'sl%') DESC
        LIMIT 1
    ) img ON TRUE
)
SELECT b.commander,
       b.kept_value,
       b.affinity_value,
       b.kept_count,
       b.lost_color_count,
       b.lost_archetype_count,
       c.total_decks,
       ci.ci AS color_identity,
       im.image_normal AS commander_image,
       JSON_AGG(
           JSON_BUILD_OBJECT(
               'name',           pc.display_name,
               'price',          ROUND(pc.price::numeric, 2),
               'inclusion_rate', ROUND(pc.inclusion_rate::numeric, 1),
               'status',         pc.status
           ) ORDER BY pc.price DESC, pc.display_name
       ) AS cards
FROM best b
JOIN per_card pc  ON pc.commander = b.commander
JOIN candidates c ON c.commander = b.commander
JOIN cmd_ci ci    ON ci.commander = b.commander
LEFT JOIN cmd_img im ON im.commander = b.commander
GROUP BY b.commander, b.kept_value, b.affinity_value, b.kept_count,
         b.lost_color_count, b.lost_archetype_count, c.total_decks, ci.ci,
         im.image_normal
ORDER BY CASE WHEN :sort = 'affinity' THEN b.affinity_value
              ELSE b.kept_value END DESC,
         b.kept_count DESC
"""


# ── Requête 3 : cartes les plus chères que le candidat joue et qui manquent ───
_MISSING_SQL = """
WITH cand AS (
    SELECT unnest(CAST(:cmds AS TEXT[])) AS commander
),
deck_names AS (
    SELECT unnest(CAST(:deck_names AS TEXT[])) AS card_lower
),
pool AS (
    -- Cartes réellement jouées par le candidat, hors son propre nom (ou celui
    -- de son partenaire), qui n'est pas une carte à acheter pour le deck
    SELECT ds.commander, ds.inclusion_rate, LOWER(BTRIM(ds.card_name)) AS card_lower
    FROM deck_stat_commander ds
    JOIN cand c ON c.commander = ds.commander
    WHERE ds.inclusion_rate > :missing_min_inclusion
      AND LOWER(BTRIM(ds.card_name)) <> ALL(
          SELECT LOWER(BTRIM(part))
          FROM unnest(string_to_array(REPLACE(c.commander, ' & ', ' / '), ' / ')) AS part
      )
),
missing AS (
    SELECT p.*
    FROM pool p
    WHERE NOT EXISTS (SELECT 1 FROM deck_names d WHERE d.card_lower = p.card_lower)
),
resolved AS (
    SELECT DISTINCT ON (m.commander, m.card_lower)
           m.commander, m.card_lower, m.inclusion_rate,
           sc.id AS card_id, sc.name AS display_name
    FROM missing m
    JOIN scryfall_cards sc ON sc.normalized_name = m.card_lower
    ORDER BY m.commander, m.card_lower, sc.id
),
last_date AS (
    SELECT MAX(date) AS d
    FROM scryfall_card_prices
    WHERE currency = 'eur' AND price_type = 'regular'
),
prices AS (
    -- Agrégat sur la seule dernière date de relevé (index ix_scryfall_card_prices_date) :
    -- plus rapide qu'un lookup par carte quand le pool dépasse quelques centaines d'entrées
    SELECT pr.card_id, MIN(p.price) AS eur_price
    FROM scryfall_card_printings pr
    JOIN scryfall_card_prices p ON p.printing_id = pr.id
    WHERE p.currency = 'eur' AND p.price_type = 'regular' AND p.price > 0
      AND p.date = (SELECT d FROM last_date)
    GROUP BY pr.card_id
),
ranked AS (
    SELECT r.commander, r.card_id, r.display_name, r.inclusion_rate,
           COALESCE(pz.eur_price, 0) AS price,
           ROW_NUMBER() OVER (
               PARTITION BY r.commander
               ORDER BY COALESCE(pz.eur_price, 0) DESC, r.inclusion_rate DESC
           ) AS rk
    FROM resolved r
    LEFT JOIN prices pz ON pz.card_id = r.card_id
)
SELECT rk_.commander, rk_.display_name, rk_.price, rk_.inclusion_rate,
       img.image_normal
FROM ranked rk_
LEFT JOIN LATERAL (
    SELECT p.image_normal
    FROM scryfall_card_printings p
    WHERE p.card_id = rk_.card_id
      AND p.image_normal IS NOT NULL
      AND p.lang = 'en'
    ORDER BY (p.set_code NOT ILIKE 'sl%') DESC, p.released_at DESC NULLS LAST, p.id
    LIMIT 1
) img ON TRUE
WHERE rk_.rk <= :missing_top
ORDER BY rk_.commander, rk_.price DESC
"""


def _f(value) -> float:
    """Decimal / None → float."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _normalized(name: str) -> str:
    """Meme regle que scryfall_cards.normalized_name (accents supprimes)."""
    from mtgdb.db.models.card import normalize_card_name
    return normalize_card_name(name or "")


def load_deck_cards(user_id: int, commander: str) -> list[tuple[str, int]]:
    """Cartes du deck de ce commandant, commandant lui-même exclu."""
    cmd_lower = _normalized(commander)
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT card_name, quantity
            FROM user_deck_cards
            WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
        """), {"uid": user_id, "cmd": commander}).fetchall()

    cards: list[tuple[str, int]] = []
    for row in rows:
        name = (row.card_name or "").strip()
        if not name or _normalized(name) == cmd_lower:
            continue
        cards.append((name, int(row.quantity or 1)))
    return cards


def analyze_deck(
    session,
    cards: list[tuple[str, int]],
    staple_threshold: float,
) -> list[dict]:
    """Résout chaque carte (identité couleur, prix EUR, fréquence globale)."""
    names = [_normalized(name) for name, _ in cards]
    qtys = [qty for _, qty in cards]

    rows = session.execute(text(_DECK_SQL), {"names": names, "qtys": qtys}).fetchall()

    analyzed: list[dict] = []
    for row in rows:
        ci = list(row.ci or [])
        type_line = row.type_line or ""
        is_basic = (
            type_line.lower().startswith("basic land")
            or row.card_lower in BASIC_LANDS
        )
        analyzed.append({
            "card_lower":       row.card_lower,
            "name":             row.display_name or row.card_lower,
            "qty":              int(row.qty or 1),
            "color_identity":   ci,
            "price":            _f(row.price),
            "global_frequency": _f(row.global_frequency),
            "resolved":         bool(row.is_resolved),
            "image":            row.image_normal,
            "is_staple":        is_basic or _f(row.global_frequency) > staple_threshold,
        })
    return analyzed


def suggest_swaps(
    user_id: int,
    commander: str,
    top: int = 10,
    staple_threshold: float = DEFAULT_STAPLE_THRESHOLD,
    sort: str = "value",
    max_colors: int = 5,
    min_inclusion: float = DEFAULT_MIN_INCLUSION,
    missing_min_inclusion: float = MISSING_MIN_INCLUSION,
) -> dict:
    """
    Classe les commandants alternatifs pour le deck de `commander`.

    sort="value"    : valeur EUR conservée (les identités 5 couleurs dominent,
                      puisqu'elles ne perdent aucune carte pour raison de couleur) ;
    sort="affinity" : même valeur pondérée par le taux d'inclusion réel, ce qui
                      remonte les commandants qui jouent vraiment ces cartes.
    max_colors      : nombre maximum de couleurs de l'identité du candidat ;
    min_inclusion   : taux d'inclusion minimal (%) pour qu'une carte compte
                      comme conservée ;
    missing_min_inclusion : taux d'inclusion au-delà duquel une carte absente
                      du deck est proposée à l'achat (triée par prix).

    Retourne un dict prêt à sérialiser (voir le router commander_swap).
    """
    sort = "affinity" if sort == "affinity" else "value"
    max_colors = max(1, min(5, max_colors))
    min_inclusion = max(0.0, min(100.0, min_inclusion))
    missing_min_inclusion = max(0.0, min(100.0, missing_min_inclusion))
    cards = load_deck_cards(user_id, commander)
    if not cards:
        return {
            "commander": commander,
            "error": "Ce deck ne contient aucune carte.",
            "candidates": [],
        }

    with SessionLocal() as sess:
        analyzed = analyze_deck(sess, cards, staple_threshold)

        scored = [c for c in analyzed if not c["is_staple"]]
        staples = [c for c in analyzed if c["is_staple"]]
        unresolved = [c["name"] for c in analyzed if not c["resolved"]]
        no_price = [c["name"] for c in scored if c["price"] <= 0]

        scored_value = round(sum(c["price"] for c in scored), 2)
        deck_value = round(sum(c["price"] for c in analyzed), 2)

        images_by_name = {c["name"]: c["image"] for c in analyzed}

        candidates: list[dict] = []
        if scored:
            rows = sess.execute(text(_SWAP_SQL), {
                "names":    [c["card_lower"] for c in scored],
                "displays": [c["name"] for c in scored],
                "prices":   [c["price"] for c in scored],
                "cis":      ["".join(c["color_identity"]) for c in scored],
                "current":    commander,
                "top":        top,
                "sort":          sort,
                "max_colors":    max_colors,
                "min_inclusion": min_inclusion,
            }).fetchall()

            for row in rows:
                kept_value = round(_f(row.kept_value), 2)
                cards_by_status: dict[str, list[dict]] = {
                    "kept": [], "color": [], "archetype": [],
                }
                for card in (row.cards or []):
                    cards_by_status.setdefault(card["status"], []).append({
                        "name":           card["name"],
                        "price":          _f(card["price"]),
                        "inclusion_rate": _f(card["inclusion_rate"]),
                        "image":          images_by_name.get(card["name"]),
                    })

                candidates.append({
                    "commander":            row.commander,
                    "missing_cards":        [],
                    "missing_value":        0.0,
                    "commander_image":      row.commander_image,
                    "color_identity":       list(row.color_identity or []),
                    "total_decks":          int(row.total_decks or 0),
                    "kept_value":           kept_value,
                    "affinity_value":       round(_f(row.affinity_value), 2),
                    "kept_pct":             round(100 * kept_value / scored_value, 1) if scored_value else 0.0,
                    "kept_count":           int(row.kept_count or 0),
                    "lost_color_count":     int(row.lost_color_count or 0),
                    "lost_archetype_count": int(row.lost_archetype_count or 0),
                    "cards":                cards_by_status,
                })

        if candidates:
            by_commander = {c["commander"]: c for c in candidates}
            missing_rows = sess.execute(text(_MISSING_SQL), {
                "cmds":          list(by_commander.keys()),
                "deck_names":    [c["card_lower"] for c in analyzed],
                "missing_min_inclusion": missing_min_inclusion,
                "missing_top":           MISSING_TOP,
            }).fetchall()

            for row in missing_rows:
                candidate = by_commander.get(row.commander)
                if candidate is None:
                    continue
                candidate["missing_cards"].append({
                    "name":           row.display_name,
                    "price":          _f(row.price),
                    "inclusion_rate": _f(row.inclusion_rate),
                    "image":          row.image_normal,
                })

            for candidate in candidates:
                candidate["missing_value"] = round(
                    sum(c["price"] for c in candidate["missing_cards"]), 2
                )

    return {
        "commander":        commander,
        "deck_card_count":  len(analyzed),
        "deck_value":       deck_value,
        "scored_count":     len(scored),
        "scored_value":     scored_value,
        "staple_threshold": staple_threshold,
        "sort":             sort,
        "max_colors":       max_colors,
        "min_inclusion":    min_inclusion,
        "missing_top":      MISSING_TOP,
        "missing_min_inclusion": missing_min_inclusion,
        "staples": [
            {"name": c["name"], "price": c["price"], "global_frequency": c["global_frequency"]}
            for c in sorted(staples, key=lambda c: -c["price"])
        ],
        "unresolved":       unresolved,
        "no_price_count":   len(no_price),
        "candidates":       candidates,
    }
