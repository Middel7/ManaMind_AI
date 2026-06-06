# Commander Profile Builder — Documentation

## Présentation

Le Commander Profile Builder génère automatiquement un profil stratégique structuré pour n'importe quel commandant Magic: The Gathering. Ce profil est utilisé par le moteur de recommandation V2 pour orienter les suggestions de cartes.

---

## Architecture

```
src/recommendations/commander_profile/
  profile_builder.py          # Point d'entrée principal
  oracle_feature_extractor.py # Analyse du texte Oracle
  archetype_detector.py       # Détection des archétypes MTG
  decklist_strategy_analyzer.py # Analyse des decklists existantes
  profile_schema.py           # Schéma et constructeurs du profil
  profile_cache.py            # Gestion des chemins et de la persistance

data/recommendation_config/
  oracle_patterns.json        # Patterns de détection dans oracle_text
  archetype_templates.json    # Templates des 28+ archétypes MTG
  role_taxonomy.json          # Taxonomie des rôles de cartes

data/commander_profiles/
  manual/                     # Profils créés à la main (priorité absolue)
  generated/                  # Profils générés automatiquement (cache)
```

---

## Ordre de priorité

```
1. data/commander_profiles/manual/<slug>.json       ← PRIORITÉ ABSOLUE
2. data/commander_profiles/generated/<slug>.json    ← si frais (< 30 jours)
3. Génération automatique via oracle + decklists    ← sinon
```

Aucun commandant n'a de traitement spécial codé dans le code. **Tout profil spécifique doit être un fichier JSON manuel.**

---

## Ajouter un profil manuel

1. Créer le fichier : `data/commander_profiles/manual/<slug>.json`
   - Le slug est le nom normalisé : minuscules, sans accents, espaces → `_`, ponctuation supprimée
   - Exemples :
     - "Galadriel, Light of Valinor" → `galadriel_light_of_valinor.json`
     - "Shadowfax, Lord of Horses" → `shadowfax_lord_of_horses.json`
     - "Muldrotha, the Gravetide" → `muldrotha_the_gravetide.json`

2. Structure minimale requise :
```json
{
  "source": "manual",
  "primary_strategy": "creature_etb_value",
  "strategy_confidence": 0.95,
  "secondary_strategies": ["blink", "token_generation"],
  "wanted_roles": ["etb_synergy", "blink", "card_draw", "ramp", "protection", "removal"],
  "avoided_roles": ["graveyard_synergy", "high_mana_low_impact"],
  "preferred_card_types": ["Creature", "Enchantment", "Instant"],
  "max_preferred_mana_value": 5,
  "score_weights": {
    "decklist_popularity": 0.25,
    "strategic_role": 0.25,
    "commander_synergy": 0.20,
    "vector_similarity": 0.15,
    "edhrec": 0.10,
    "mana_curve": 0.03,
    "card_quality": 0.02
  }
}
```

3. Le profil sera utilisé immédiatement au prochain lancement, sans reconstruction.

---

## Forcer la reconstruction du profil généré

```bash
python "scripts/Recommandations vectorielles V2.py" --commander "Shadowfax, Lord of Horses" --rebuild-cache
python scripts/test_commander_profile_builder.py --commander "Shadowfax, Lord of Horses" --rebuild
```

Le profil manuel n'est **jamais** reconstruit automatiquement — seuls les profils générés peuvent être forcés.

---

## Comment les archétypes sont détectés

### Étape 1 — Extraction des features Oracle

`oracle_feature_extractor.py` lit le texte oracle du commandant et détecte :

- **Triggers** : ce qui déclenche la capacité (`attacks`, `creature_enters`, `casts_spell`…)
- **Actions** : ce que fait la capacité (`draw_cards`, `create_tokens`, `cheat_from_hand`…)
- **Contraintes** : restrictions sur les cartes ciblées (`lesser_power`, `from_hand`, `mana_value_less_or_equal`…)
- **Zones** : battlefield, hand, graveyard, library, exile
- **Contraintes numériques** : valeurs extraites par regex ("mana value 3 or less" → `mana_value_le_3`)

Exemple pour Shadowfax :
```
Texte : "Whenever Shadowfax attacks, you may put a creature card with lesser power from your hand onto the battlefield tapped and attacking."

→ triggers: [attacks, attack_required]
→ actions: [cheat_from_hand, cheat_creatures]
→ constraints: [lesser_power, power_less_than_commander, creature_only, from_hand, attack_required]
→ zones: [hand, battlefield]
```

### Étape 2 — Comparaison aux templates d'archétypes

`archetype_detector.py` compare les features Oracle aux signaux de chaque archétype défini dans `archetype_templates.json`.

Score oracle = proportion de signaux matchés, avec bonus si plusieurs signaux forts.
Score decklist = présence des rôles de l'archétype dans les decklists disponibles.
Score total = 70% oracle + 30% decklist.

### Étape 3 — Fusion des archétypes

Les 3 meilleurs archétypes sont fusionnés pour produire :
- `wanted_roles` : union pondérée des rôles voulus
- `avoided_roles` : union pondérée des rôles évités
- `preferred_card_types` : types classés par pertinence agrégée

### Étape 4 — Calcul de max_preferred_mana_value

Basé sur l'archétype primaire et les features Oracle :

| Condition | Valeur |
|-----------|--------|
| Cheat de mana (`without paying`, `from hand onto battlefield`) | 8 |
| Ramp lourd (big_mana archétype) | 8-9 |
| Spellslinger, voltron | 4-5 |
| Archétype aggro | 3-4 |
| MV commandant ≥ 7 | 7 |
| MV commandant ≤ 2 | 4 |
| Contrainte `mana_value_le_X` dans oracle | X |
| Défaut | 5 |

---

## Comment les decklists sont utilisées

Le module cherche automatiquement un sous-dossier dans `data/Decklists/` dont le nom correspond au commandant.

- Si trouvé : parse tous les fichiers `.csv` et `.txt`, calcule la fréquence de chaque carte
- Si non trouvé : score decklist_popularity = 0.5 (neutre) pour toutes les cartes

Le cache de l'analyse est dans :
`data/recommendation_cache/commander_profile_decklist_analysis_<slug>.json`

---

## Contraintes deckbuilding

Le module extrait des contraintes exploitables par le scoring :

| Contrainte | Description |
|-----------|-------------|
| `creature_power_less_than_commander_power` | Ne met en jeu que des créatures moins puissantes que le commandant |
| `needs_commander_to_attack` | La capacité se déclenche en attaquant |
| `wants_creatures_in_hand` | Nécessite des créatures disponibles en main |
| `wants_high_impact_creatures` | Met en jeu gratuitement → favoriser les grosses créatures |
| `wants_creatures_to_enter_battlefield` | Déclenche un ETB |
| `wants_card_draw_triggers` | Bénéficie de la pioche pour alimenter la main |
| `wants_tokens` | Synergise avec les tokens |
| `wants_graveyard_filled` | Le cimetière est une ressource |
| `wants_instant_sorcery_density` | Spellslinger / prowess |

---

## Archetypes disponibles

28 archétypes sont définis dans `archetype_templates.json` :

`cheat_creatures`, `attack_trigger`, `creature_etb_value`, `token_strategy`, `go_wide`, `counter_strategy`, `voltron`, `spellslinger`, `aristocrats`, `graveyard_recursion`, `reanimator`, `blink`, `landfall`, `artifact_synergy`, `enchantment_synergy`, `tribal`, `combat_damage`, `draw_matter`, `stax`, `lifegain`, `big_mana`, `control`, `low_curve_aggro`, `high_impact_creatures`, `aura_equipment`, `mill`, `sacrifice`, `good_stuff`

`good_stuff` est le fallback et ne s'active que si aucun autre archétype n'atteint 0.2 de confiance.

---

## Limites connues

1. **Détection textuelle** : basée sur des patterns de chaînes — ne comprend pas réellement la sémantique MTG
2. **Cartes à double face** : l'oracle_text peut être incomplet si seule une face est stockée
3. **Synergies non textuelles** : certains archétypes (e.g. "storm", "infect") ne sont pas détectés
4. **Decklists manquantes** : sans decklists, le profil se base uniquement sur Oracle
5. **Profil générique insuffisant** : pour les commandants complexes, un profil manuel reste plus précis

---

## Script de test

```bash
python scripts/test_commander_profile_builder.py --commander "Shadowfax, Lord of Horses"
python scripts/test_commander_profile_builder.py --commander "Galadriel, Light of Valinor"
python scripts/test_commander_profile_builder.py --commander "Muldrotha, the Gravetide"
python scripts/test_commander_profile_builder.py --commander "Shadowfax, Lord of Horses" --rebuild
```
