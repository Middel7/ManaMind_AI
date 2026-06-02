# Résumé du PPT - Projet IA MTG Recommandation

Ce document est un résumé automatique de `docs/Projet_IA_MTG_Recommandation.pptx`.

> Pour rafraîchir ce résumé : `python src/manamind/refresh_ppt_summary.py`

## Diapositive 1
- Projet
- IA –
- Recommandation
- de
- cartes
- MTG
- pour commander
- Plan détaillé et points clés à considérer

## Diapositive 2
- Données
- nécessaires
- Story
- Lore

## Diapositive 3
- Données
- nécessaires
- Game
- Lore

## Diapositive 4
- Modélisation
- du
- problème
- Collaborative Filtering : cooccurrence entre decks
- Amélioration de deck existant
- Création de decks en fonction commandant
- Budget
- Stratégies
- Content-Based :
- similarité
- entre
- textes
- ou
- effets
- Préconisation de cartes à venir
- Citer les cartes à retirer
- Modèles
- hybrides
- graphe
- de
- synergie
- (Node2Vec)
- Deep
- learnin
- g : NLP ou GNN pour synergies complexes

## Diapositive 5
- 5. Évaluation
- Masquer 10% d’un deck et tenter de le compléter
- Utiliser précision, rappel, NDCG
- Recueillir des retours de vrais joueurs

## Diapositive 6
- Pipeline technique
- Collecte
- N
- ettoyage
- des données
- Feature engineering
- Stockage DB
- Google drive stockage CSV DECKLIST
- Postgre
- = DB SQL
- Control
- Vérification de duplicata
- Modèle
- de recommendation
- GITHUB = Stockage des scripts qui interagissent avec le model
- Interface (API
- ou
- interface web)
- Interface input
- Interface output site web ou app
- Déploiement
- (Docker, cloud)

## Diapositive 7
- MVP

## Diapositive 8
- Phase 4 – Interface utilisateur
- Interface Flask
- ou
- Streamlit
- Python compatible HTML pour Front end
- Upload deck
- copier-
- coller
- Afficher
- recommandations
- + images
- Export CSV
- vers
- Moxfield

## Diapositive 9
- Amélioration de deck existant
- Top 10
- recommended
- cards
- SOL RING
- PLAIN
- ….
- Add
- you
- decklist
- Adaptive
- Automaton
- Admiral
- Beckett
- Brass
- Brass,Unsinkable
- Admiral's
- Order
- Adéwalé
- , Breaker of
- Chains
- Arcane Signet
- Bident of
- Thassa
- Black
- Market
- Connections
- TO BE REMOVED
- Budget
- Low
- Average
- No
- limit
- Top Synergie / Combo
- Pauper
- Yes
- SAVE
- Land vs
- Your
- deck lands

## Diapositive 10
- Création de decks en fonction commandant
- SAVE
- Top 100
- recommended
- cards
- SOL RING
- PLAIN
- ….
- Commander
- Adaptive
- Automaton
- Budget
- Low
- Average
- No
- limit
- User validation
- Y
- N

## Diapositive 11
- Préconisation de cartes à venir
- SAVE
- Top 10
- recommended
- cards
- SOL RING
- PLAIN
- ….
- Commander
- Adaptive
- Automaton
- Budget
- Low
- Average
- No
- limit
- User validation
- Y
- N

## Diapositive 12
- git
- fetch
- git pull
- add
- .
- git commit –m «Fabien»
- git push

## Diapositive 13
- Génération de story sur la base d’une
- decklist
- SAVE
- Add
- you
- Adaptive
- Automaton
- Admiral
- Beckett
- Brass
- Brass,Unsinkable
- Admiral's
- Order
- Adéwalé
- , Breaker of
- Chains
- Arcane Signet
- Bident of
- Thassa
- Black
- Market
- Connections
- Story
- "Les Larmes de l'
- Oeil
- Azuré"Dans
- un coin oublié du Multivers, les flots rugissent autour d’un archipel dissimulé par les brumes du
- Rogue's
- Passage. Ces eaux sont le territoire des pirates, gouvernées par
- ,
- Unsinkable
- , capitaine d’une flotte hétéroclite de pillards, de revenants et de créatures mutées par la mer elle-même. À son côté, ses seconds de légende :
- Breeches
- , the
- Blastmaker
- , et Malcolm, the
- Eyes
- , une étrange alliance entre brutalité et ruse
- ailée.Leur
- objectif : découvrir la
- Treasure
- Vault, un sanctuaire englouti contenant l’artefact mythique
- Mechanized
- Production, capable de transformer chaque trésor en armée. Mais l’artefact est protégé par une malédiction : Revel in Riches. Quiconque le touche sans offrir un tribut de sang est consumé par ses propres
- désirs.Le
- périple commence à Command Tower, où la rumeur du trésor est transmise par
- Crafty
- Cutpurse
- , un espion égaré du
- Dimir
- Signet. En route, la flotte traverse les abysses hantés de
- Takenuma
- Abandoned
- Mire, affronte les illusions des Temple of
- Deceit
- , et croise les golems réveillés d’
- Urza’s
- Incubator.Mais
- dans l’ombre, un autre pirate agit :
- Ragavan
- Nimble
- Pilferer
- , qui s’empare du journal de bord du capitaine, révélant le secret du
- Nephalia
- Drownyard
- , le seul passage vers la chambre immergée. La trahison ne tarde pas. Francisco,
- Fowl
- Marauder, un marin autrefois fidèle, sabote le Port
- Razer
- à la solde de
- Vraska’s
- Contempt
- , une prêtresse assoiffée de
- contrôle.Alors
- que la flotte prend l’eau,
- se dresse avec l’autorité d’un mythe : elle invoque les tempêtes de
- Cyclonic
- Rift, libère la magie de
- Blasphemous
- Act
- , et réanime les morts via Black
- Connections. Le ciel s’ouvre, dévoilant The
- Indomitable
- , leur navire-fantôme
- légendaire.Dans
- un ultime assaut,
- Jackdaw
- et Swan Song détournent l’attention des gardiens du sanctuaire, tandis que Zara,
- Renegade
- Recruiter
- rallie les créatures englouties. À l’aide de
- Urza's
- Incubator
- , l’artefact est enfin
- maîtrisé.Mais
- le prix à payer est lourd. L’âme d’
- est liée à jamais au sanctuaire, gardienne éternelle de la
- Kindred
- Discovery, veillant sur l’artefact et empêchant que le cycle de cupidité ne recommence.

## Diapositive 14
- Génération vidéo sur la base d’une
- decklist
- SAVE
- Add
- you
- Adaptive
- Automaton
- Admiral
- Beckett
- Brass
- Brass,Unsinkable
- Admiral's
- Order
- Adéwalé
- , Breaker of
- Chains
- Arcane Signet
- Bident of
- Thassa
- Black
- Market
- Connections
- Video

## Diapositive 15
- Mini jeu « Trouver la carte »
- SAVE
- Chatbot
- feedback
- input
- Congratulation
- Points
- earned

## Diapositive 16
- Phase 5 –
- Améliorations
- Step
- 2
- Utilisateurs feedback sur les propositions
- 3
- Leaderboard
- jeu

## Diapositive 17
- Couts
- Hébergement DB
- Hébergement model Back end
- Hébergement Font end
- Cout LLM (Jeu, Image, story,
- video
- )
- Cout data

## Diapositive 18
- Business case
- Partenariats affiliés / liens d’achat de cartes
- Modèle Premium
- Publicité ciblée
- Evènements
- Other
- :
- Données &amp; trafic
- API payante pour les développeurs tiers

## Diapositive 19
- POC scope

## Diapositive 20
- Action log POC
- Fab
- Creation
- compte
- github
- Archétype commandants (600)
- Archétype Carte few shot
- promting
- Formation
- vscode
- https://youtu.be/dutyOc_cAEU?si=zkAO3sI0Kc-32dOW
- Structurer le prompt pour le story
- creation
- Hedredo
- repo GITHUB
- Constrution
- DB
- Nettoyage data +
- feature
- engineering
- Modélisation
- Metric
- évaluation à définir (Feedback utilisateur)
- Interface front end avec API back end

## Diapositive 21
- 1. Objectif du système
- Main :
- Amélioration de deck existant
- Cartes à ajouter
- Citer les cartes à retirer
- Création de decks en fonction commandant
- Budget
- Stratégies
- Préconisation de cartes à venir
- Additionnal
- :
- Génération de story sur la base d’une
- decklist
- Génération vidéo sur la base d’une
- Mini jeu « Trouver la carte »

## Diapositive 22
- Données
- nécessaires
- SCRAP
- Decklists
- :
- Moxfield
- ,
- EDHRec
- MTGGoldfish
- Obtenir sur de data actualisée
- Cartes :
- Scryfall
- API (
- coût
- , couleur,
- texte
- ...)
- https://scryfall.com/docs/api
- Combien de temps avant la sortie on a le détail des cartes
- Nom de la carte
- Quantité
- Commander Y/N
- Date de
- creation
- Date d’update
- Cout du deck
- Views
- Likes
- Lore

## Diapositive 23
- Amélioration de deck existant

## Diapositive 24
- Content base vs Collaborative
- filtering
- Popularité
- Par commandant
- Co occurrence (Combos, paires de cartes)
- Content base
- Utilisateur = Commandant
- Two
- -Tower Model
- Tour de gauche Commandant (Data du
- scrapping
- )
- Tour de droite Cartes (Dara du
- Json
- et
- scryfall

## Diapositive 25
- Content base
- Utilisateur = Commandant
- Profil commandant
- Archétype Commandant
- Colors
- Cartes
- Archétique

## Diapositive 26
- Ecarté
- Collaborative
- filtering
- Deux commandants qui pourraient être proches peuvent utiliser des cartes complètement différentes et les suggestions basées sur le proximité des commandants ne paraissent pas pertinentes

## Diapositive 27
- Données nécessaires Master data
- https://mtgjson.com/data-models/card/card-atomic/
- https://scryfall.com/docs/api
- Regarder les meilleures sources de MD

---

*Résumé généré automatiquement depuis `docs/Projet_IA_MTG_Recommandation.pptx`.*