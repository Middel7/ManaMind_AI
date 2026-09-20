"""L'écran du catalogue : qui peut agir, et ce que la machine sait faire.

Le module lance des sous-processus — pipeline MTG-DB, script de tirage. Ces
tests ne lancent rien : ils vérifient les deux choses qui se décident avant, et
qui sont les seules à pouvoir mal tourner sans qu'on le voie.

D'abord l'accès : ces routes exécutent des commandes sur la machine, elles ne
doivent répondre qu'à un administrateur authentifié.

Ensuite le choix des actions. `_actions()` déclare une commande fixe par action
et refuse tout ce qui n'y figure pas : c'est ce qui garantit qu'aucune saisie ne
parvient à un shell.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from manamind.routers.admin_catalogue import _actions


def test_lire_l_etat_du_catalogue_exige_un_administrateur(client: TestClient):
    r = client.get("/api/admin/catalogue")
    assert r.status_code in (401, 403), f"Attendu 401/403, obtenu {r.status_code}: {r.text}"


def test_lancer_une_action_exige_un_administrateur(client: TestClient):
    r = client.post("/api/admin/catalogue/run", json={"action": "tokens"})
    assert r.status_code in (401, 403), f"Attendu 401/403, obtenu {r.status_code}: {r.text}"


def test_arreter_exige_un_administrateur(client: TestClient):
    r = client.post("/api/admin/catalogue/stop")
    assert r.status_code in (401, 403), f"Attendu 401/403, obtenu {r.status_code}: {r.text}"


def test_les_trois_actions_sont_declarees():
    assert set(_actions()) == {"pull", "tokens", "import"}


def test_une_action_indisponible_dit_pourquoi():
    """Un bouton grisé sans motif enverrait chercher la cause dans les journaux."""
    for cle, action in _actions().items():
        if not action["available"]:
            assert action["reason"], f"« {cle} » est indisponible sans motif"


def test_aucune_commande_ne_passe_par_un_shell():
    """Les commandes sont des listes d'arguments, jamais une ligne à interpréter.

    C'est ce qui rend l'exécution sûre : `subprocess` reçoit un tableau, et
    l'action demandée n'est qu'une clé cherchée dans ce dictionnaire.
    """
    for cle, action in _actions().items():
        commande = action["command"]
        assert isinstance(commande, list), f"« {cle} » ne porte pas une liste d'arguments"
        assert all(isinstance(arg, str) for arg in commande)
