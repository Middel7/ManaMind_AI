"""Router d'administration du catalogue Magic.

ManaMind ne fabrique pas le catalogue, il le recoit — et jusqu'ici, aller
chercher sa derniere version demandait une session SSH ou une tache planifiee.
Ce module met ce geste derriere un bouton, sans changer qui produit quoi.

La SOURCE depend de la machine, et c'est la seule difference :

    serveur en ligne   la base partagee Render, via deploy/pull_catalogue.sh
    poste de travail   le pipeline MTG-DB, qui ecrit directement dans la base

Rien n'est devine : chaque action declare ce qu'il lui faut — un script, un
interpreteur, une variable d'environnement — et se dit indisponible avec le
motif exact quand il manque. C'est ce que fait deja `_require_offline_tools`
pour le scraping en production, et cela evite qu'un bouton echoue au fond d'un
thread sur une erreur incomprehensible.

Aucune saisie de l'utilisateur ne parvient a un shell : l'action demandee est
comparee a un dictionnaire de commandes fixes, et les sous-processus sont lances
sans `shell=True`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from manamind.auth import require_admin
from manamind.db.engine import SessionLocal

from ._shared import ROOT, _json_response

log = logging.getLogger("manamind.catalogue")

router = APIRouter()


# ── Ou vit le depot MTG-DB ───────────────────────────────────────────────────

def _mtgdb_dir() -> Path | None:
    """Le depot MTG-DB sur cette machine, s'il y est.

    MTGDB_DIR le designe explicitement ; sinon on regarde le dossier voisin, ou
    il se trouve sur le poste de travail (les deux depots sont clones cote a
    cote). Sur le serveur, il n'y est pas — et c'est normal : le catalogue y
    arrive par copie, pas par import.
    """
    candidat = os.environ.get("MTGDB_DIR")
    chemins = [Path(candidat)] if candidat else []
    chemins.append(ROOT.parent / "MTG-DB")
    for chemin in chemins:
        if (chemin / "scripts" / "import_scryfall.py").is_file():
            return chemin
    return None


def _mtgdb_python(depot: Path) -> Path | None:
    """L'interpreteur du venv de MTG-DB.

    Celui de ManaMind ne conviendrait pas : le pipeline d'import a ses propres
    dependances (httpx, tqdm, ijson), absentes d'ici.
    """
    for relatif in (Path(".venv/Scripts/python.exe"), Path(".venv/bin/python")):
        interpreteur = depot / relatif
        if interpreteur.is_file():
            return interpreteur
    return None


# ── Les trois actions ────────────────────────────────────────────────────────

def _actions() -> dict[str, dict]:
    """Decrit les actions et dit, pour chacune, si cette machine sait la faire.

    Construit a chaque appel plutot que fige au demarrage : sur le poste, le
    venv de MTG-DB peut apparaitre entre deux ouvertures de l'ecran.
    """
    depot = _mtgdb_dir()
    interpreteur = _mtgdb_python(depot) if depot else None
    script_tirage = ROOT / "deploy" / "pull_catalogue.sh"
    url_catalogue = os.environ.get("CATALOGUE_DATABASE_URL", "").strip()
    url_cible = os.environ.get("DATABASE_URL", "").strip()

    actions: dict[str, dict] = {}

    # ── Tirer le catalogue depuis la base partagee ───────────────────────────
    manques: list[str] = []
    if not url_catalogue:
        manques.append("CATALOGUE_DATABASE_URL n'est pas défini")
    if not url_cible:
        manques.append("DATABASE_URL n'est pas défini")
    if not script_tirage.is_file():
        manques.append("deploy/pull_catalogue.sh est absent")
    if shutil.which("pg_dump") is None or shutil.which("psql") is None:
        manques.append("pg_dump et psql ne sont pas installés ici")
    if shutil.which("bash") is None:
        manques.append("bash est absent")
    actions["pull"] = {
        "label": "Tirer le catalogue partagé",
        "description": "Copie cartes, éditions, jetons et cotes depuis la base "
                       "partagée. Quelques minutes, pendant lesquelles les tables "
                       "du catalogue sont recréées : les écrans qui les lisent "
                       "restent vides le temps de l'opération.",
        "available": not manques,
        "reason": " ; ".join(manques),
        "command": ["bash", str(script_tirage), "--yes"],
        "cwd": str(ROOT),
        "env": {"CATALOGUE_DATABASE_URL": url_catalogue, "TARGET_DATABASE_URL": url_cible},
        # Le tirage recree les tables du catalogue : trois heures de marge, bien
        # au-dela des quelques minutes observees, mais un processus bloque sur
        # une base injoignable ne doit pas rester la indefiniment.
        "timeout": 3 * 3600,
    }

    # ── Completer les jetons depuis le bulk deja telecharge ──────────────────
    manques = []
    if depot is None:
        manques.append("le dépôt MTG-DB est introuvable sur cette machine")
    elif interpreteur is None:
        manques.append(f"aucun environnement Python dans {depot}")
    actions["tokens"] = {
        "label": "Compléter les jetons",
        "description": "Relit le dernier fichier Scryfall téléchargé et n'écrit "
                       "que les liaisons carte → jeton manquantes. Moins d'une minute.",
        "available": not manques,
        "reason": " ; ".join(manques),
        "command": ([str(interpreteur), "scripts/backfill_card_parts.py"]
                    if interpreteur else []),
        "cwd": str(depot) if depot else "",
        "env": {},
        "timeout": 3600,
    }

    # ── Import complet du catalogue ──────────────────────────────────────────
    actions["import"] = {
        "label": "Import complet",
        "description": "Télécharge le fichier Scryfall du jour et réécrit tout le "
                       "catalogue. Environ deux heures.",
        "available": not manques,
        "reason": " ; ".join(manques),
        "command": ([str(interpreteur), "scripts/import_scryfall.py"]
                    if interpreteur else []),
        "cwd": str(depot) if depot else "",
        "env": {},
        # Deux heures mesurees sur le poste ; quatre de plafond, pour qu'un
        # reseau lent ne fasse pas tuer un import aux trois quarts fait.
        "timeout": 4 * 3600,
    }

    return actions


# ── Le job en cours ──────────────────────────────────────────────────────────
#
# Un seul a la fois, et volontairement : les trois actions ecrivent dans les
# memes tables. En lancer deux en parallele ne diviserait rien et laisserait un
# tirage recreer les tables sous les pieds d'un import.

_LOCK = threading.Lock()
_JOB: dict | None = None
_PROCESS: subprocess.Popen | None = None


def _journaliser(job: dict, message: str) -> None:
    job["log"].append({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "msg": message,
    })
    # Le journal vit en memoire et l'ecran n'en montre que la fin : un import
    # complet produit des milliers de lignes.
    if len(job["log"]) > 600:
        del job["log"][:200]


def _executer(job: dict, action: dict) -> None:
    """Lance la commande et recopie sa sortie dans le journal du job."""
    global _PROCESS

    environnement = os.environ.copy()
    environnement.update({cle: valeur for cle, valeur in action["env"].items() if valeur})
    # Sans cela, un accent du journal de MTG-DB fait lever le sous-processus sur
    # la console Windows, en cp1252 par defaut.
    environnement["PYTHONIOENCODING"] = "utf-8"
    environnement["PYTHONUNBUFFERED"] = "1"

    try:
        processus = subprocess.Popen(
            action["command"],
            cwd=action["cwd"] or None,
            env=environnement,
            # Aucune entree : un script qui demanderait confirmation recevrait
            # une fin de fichier et s'arreterait, au lieu d'attendre une reponse
            # que personne ne peut lui donner depuis un thread du serveur.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        _journaliser(job, f"Lancement impossible : {exc}")
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        return

    _PROCESS = processus
    # Le statut se decide ici et se pose dans le `finally` : un travail dont le
    # thread meurt sans conclure resterait « en cours » pour toujours, et
    # l'ecran attendrait indefiniment un avancement qui ne viendra plus. C'est
    # arrive des le premier essai, sur une exception que rien ne rattrapait.
    statut, erreur = "error", "interrompu sans raison connue"
    try:
        for ligne in processus.stdout:  # type: ignore[union-attr]
            ligne = ligne.rstrip()
            if ligne:
                _journaliser(job, ligne)
        code = processus.wait(timeout=action["timeout"])
        if job.get("stopping"):
            statut, erreur = "stopped", None
            _journaliser(job, "Arrêté à votre demande.")
        elif code == 0:
            statut, erreur = "done", None
            _journaliser(job, "Terminé.")
        else:
            statut, erreur = "error", f"Code de sortie {code}"
            _journaliser(job, f"Échec : code de sortie {code}.")
    except subprocess.TimeoutExpired:
        processus.kill()
        statut, erreur = "error", "Délai dépassé"
        _journaliser(job, "Délai dépassé : le processus a été arrêté.")
    except Exception as exc:  # noqa: BLE001 — quelle qu'elle soit, elle doit finir le travail
        log.exception("Travail de catalogue interrompu")
        statut, erreur = "error", str(exc)
        _journaliser(job, f"Interrompu : {exc}")
        if processus.poll() is None:
            processus.kill()
    finally:
        _PROCESS = None
        job["status"] = statut
        job["error"] = erreur
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── Etat du catalogue ────────────────────────────────────────────────────────

def _etat_catalogue() -> dict:
    """Ce que la base contient, pour juger s'il y a lieu de lancer quoi que ce soit.

    `import_runs` n'est pas copie par le tirage : la fraicheur se lit donc sur
    `scryfall_cards.updated_at`, qui bouge a chaque ecriture du pipeline et
    survit a la copie.
    """
    # Une table absente ne doit pas emporter les autres. Avant le premier
    # tirage, le serveur n'a aucune table du catalogue ; apres un tirage fait
    # avant que MTG-DB ne publie scryfall_card_parts, il en manque une seule. Un
    # unique SELECT sur les quatre affichait alors « catalogue illisible » et
    # quatre tuiles vides, alors que trois chiffres etaient disponibles.
    tables = {
        "cards": "scryfall_cards",
        "printings": "scryfall_card_printings",
        "token_links": "scryfall_card_parts",
        "sets": "scryfall_mtg_sets",
    }
    etat: dict = {}
    with SessionLocal() as session:
        for cle, table in tables.items():
            # to_regclass rend NULL au lieu de lever : une seule requete suffit
            # a savoir si la table existe et, si oui, a la compter.
            existe = session.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
            ).scalar()
            etat[cle] = (
                session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                if existe else None
            )
        maj = None
        if etat["cards"] is not None:
            maj = session.scalar(text("SELECT max(updated_at) FROM scryfall_cards"))
    etat["updated_at"] = maj.isoformat() if maj else None
    return etat


def _job_public(job: dict | None) -> dict | None:
    if job is None:
        return None
    return {
        "action": job["action"],
        "label": job["label"],
        "status": job["status"],
        "started_at": job["started_at"],
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
        "stopping": bool(job.get("stopping")),
        "log": job["log"][-200:],
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/api/admin/catalogue")
def api_catalogue_state(_admin: dict = Depends(require_admin)):
    """Etat du catalogue, actions possibles sur cette machine, job en cours."""
    try:
        etat = _etat_catalogue()
    except Exception as exc:  # noqa: BLE001 — un catalogue absent n'est pas une panne
        etat = {"error": str(exc)}

    actions = [
        {
            "key": cle,
            "label": action["label"],
            "description": action["description"],
            "available": action["available"],
            "reason": action["reason"],
        }
        for cle, action in _actions().items()
    ]
    return _json_response({
        "catalogue": etat,
        "actions": actions,
        "job": _job_public(_JOB),
    })


@router.post("/api/admin/catalogue/run")
async def api_catalogue_run(request: Request, _admin: dict = Depends(require_admin)):
    """Lance une des actions du catalogue, une seule a la fois."""
    global _JOB

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corps JSON invalide") from None

    cle = (body.get("action") or "").strip()
    actions = _actions()
    if cle not in actions:
        raise HTTPException(status_code=400, detail="Action inconnue")

    action = actions[cle]
    if not action["available"]:
        # 503 et non 400 : la demande est legitime, c'est la machine qui ne sait
        # pas la satisfaire.
        raise HTTPException(status_code=503, detail=action["reason"])

    with _LOCK:
        if _JOB is not None and _JOB["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail=f"« {_JOB['label']} » est déjà en cours.",
            )
        _JOB = {
            "action": cle,
            "label": action["label"],
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "error": None,
            "stopping": False,
            "log": [],
        }
        job = _JOB

    _journaliser(job, f"{action['label']} — démarrage.")
    threading.Thread(target=_executer, args=(job, action), daemon=True).start()
    return _json_response({"started": True, "job": _job_public(job)})


@router.get("/api/admin/catalogue/job")
def api_catalogue_job(_admin: dict = Depends(require_admin)):
    """Avancement du job courant, ou du dernier termine."""
    return _json_response({"job": _job_public(_JOB)})


@router.post("/api/admin/catalogue/stop")
def api_catalogue_stop(_admin: dict = Depends(require_admin)):
    """Interrompt le job en cours.

    L'arret est brutal — le sous-processus est tue. C'est sans danger pour les
    trois actions : l'import et la completion ecrivent par lots deja valides, et
    un tirage interrompu se relance du debut, puisqu'il recree les tables.
    """
    if _JOB is None or _JOB["status"] != "running":
        raise HTTPException(status_code=400, detail="Aucune tâche en cours.")
    _JOB["stopping"] = True
    processus = _PROCESS
    if processus is not None:
        processus.kill()
    return _json_response({"stopping": True})
