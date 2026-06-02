# Documentation du projet ManaMind AI

Ce document est la documentation centrale du projet. Il contient tout ce qui doit être connu pour gérer, développer et maintenir ManaMind AI.

> Cette documentation doit systématiquement être mise à jour dès qu’une information nécessaire aux utilisateurs du projet doit être documentée.

## 1. Objectif du projet
- Construire un système de recommandation de cartes pour Magic: The Gathering en format Commander/EDH.
- Permettre l’amélioration et la création de decks selon le commandant.
- Recommander des cartes à ajouter, retirer ou surveiller.
- Produire des sorties orientées utilisateur : recommandations, histoires, vidéos et mini-jeux.

## 2. Structure du dépôt
- `data/` : données sources des decks et fichiers de référence.
- `docs/` : documentation du projet.
- `src/` : code source Python.
- `pyproject.toml` : configuration Python et dépendances.
- `uv.lock` : verrouillage des versions des dépendances.
- `.gitignore` : fichiers exclus du suivi Git.

## 3. Documentation à connaître
- `docs/PROJECT_DOCUMENTATION.md` : documentation centrale du projet.
- `docs/AGENTS.md` : directives et règles pour l’agent IA et le développement.
- `docs/Projet_IA_MTG_Recommandation.pptx` : document de contexte principal pour la stratégie produit et la feuille de route.
- `docs/Projet_IA_MTG_Recommandation.md` : résumé textuel du PPT.

## 4. Gestion des dépendances
- Toujours utiliser `uv` pour gérer les dépendances Python.
- Ne jamais utiliser `pip` directement pour installer ou désinstaller des packages dans ce projet.
- Après modification de `pyproject.toml`, exécuter `uv sync`.
- Toujours committer `pyproject.toml` et `uv.lock` ensemble.

## 5. Pratiques de développement
- Respecter le Zen of Python.
- Utiliser des annotations de type pour toutes les fonctions publiques.
- Documenter les fonctions publiques avec des docstrings (Google Style ou NumPy Style).
- Préférer une responsabilité par module.
- Ajouter des logs informatifs pour chaque étape importante.
- Maintenir le code formaté avec `ruff`.

## 6. Workflow recommandé
1. Lire `docs/Projet_IA_MTG_Recommandation.pptx` et `docs/PROJECT_DOCUMENTATION.md` pour comprendre le contexte.
2. Vérifier `pyproject.toml` pour les dépendances.
3. Ajouter ou modifier du code dans `src/manamind/`.
   - Exemple : `src/manamind/recommend_deck_changes.py` pour générer des recommandations de cartes à ajouter/retirer.
5. Mettre à jour la documentation si une information utilisateur ou développeur évolue.
6. Lancer les tests et vérifier le formatage.
7. Committer les changements avec un message clair.

## 7. Documentation et mise à jour
- Toute nouvelle fonctionnalité, changement de workflow, ajout de dépendance ou modification de structure doit être documenté ici.
- Pour les algorithmes de recommandation, le script `src/manamind/recommend_deck_changes.py` doit être décrit dans la documentation du projet.
- Les informations importantes doivent être accessibles dans `docs/PROJECT_DOCUMENTATION.md` et, si nécessaire, dans `docs/AGENTS.md`.
- Les mises à jour de la documentation sont obligatoires lorsque les utilisateurs ou développeurs ont besoin d’une information nouvelle ou modifiée.

## 8. Scripts utiles
- `python src/manamind/refresh_ppt_summary.py` : régénère le résumé Markdown du PPT.

## 9. Gestion des données
- `data/` contient les decks par commandant et les fichiers auxiliaires.
- Garder les données pertinentes et éviter de committer des fichiers temporaires inutiles.
- Documenter toute structure de données nouvelle ou modifiée.

## 10. Notes supplémentaires
- Il est recommandé d’ajouter un `README.md` racine lorsque le projet devient plus mature.
- Conserver un historique clair des décisions de projet dans la documentation.
