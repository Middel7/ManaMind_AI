# AGENTS.md
# TODO : Eviter qu'il cite des attributs hors dataset tel que artefact ou autre
- Interdire à l'agent d'utiliser uniquement dans sa réflexion des attributs de cartes, de deck ou de commandant qui ne sont pas explicitement présents dans le dataset ou la documentation associée.
- Exiger que l'agent cite toujours les sources spécifiques (fichiers, sections) de la documentation ou du code lorsqu'il fournit des réponses.
- Ne Pas faire 

# BASE DE DONNEES
- Se documenter sur la meilleur approche en POC entre un fichier db stocké sur le git ou un container docker avec des migrations alembic
- Prévoir l'ajout des tags qui constituent la meta data des cartes qui seront ajoutées dans un second temps.

# KPIS
- Faire un top des cartes avec/sans terrains de base (island, forest, mountain, plains, swamp) pour voir leur impact sur la popularité des decks
- distribution de points de mana curve par commandant (courbe de distribution avec points de mana)
- lorsqu'il fait des remplacements de cartes, soit il améliore en fonction de la mana curve expected du commandant

# CLASS CARD
- Ajouter un champs qui indique si la carte est un terrain de base (par un booléen)

# DATASET
- Lui préciser que le nom du commandant parsé depuis le nom de sous dossier doit être néttoyé des _ et des - pour correspondre aux noms dans la documentation
- Privilégier les enums ou boolées ? exacte carte commander : true/ false
- on n'est pas obligé d'avoir 100 cartes piles

# TRAIN
- Suggestion de 20 remplacements de cartes - regarder c'est quoi la meilleure métrique d'évaluation poru ce type d'approche comment je fais pour utiliser la vérité de terrain dans ce cas
- lorsqu'il fait des remplacements de cartes, soit il améliore en fonction de la mana curve expected du commandant