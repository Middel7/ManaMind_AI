#!/usr/bin/env python3
"""
start.py — Lance ManaMind avec rechargement automatique.

Usage :
    python start.py
    python start.py --no-browser   # ne pas ouvrir le navigateur
"""
import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 127.0.0.1 plutôt que localhost : ce dernier coûte une pénalité de résolution
# IPv6 à chaque connexion sous Windows.
HOST = "127.0.0.1"


def url_for(port: int) -> str:
    return f"http://{HOST}:{port}"


def open_browser(port: int, delay: float = 1.5) -> None:
    time.sleep(delay)
    url = url_for(port)
    webbrowser.open(url)
    print(f"[Start] Navigateur ouvert sur {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lance le serveur ManaMind")
    parser.add_argument("--no-browser", action="store_true", help="Ne pas ouvrir le navigateur")
    parser.add_argument("--port", type=int, default=8080, help="Port (défaut : 8080)")
    args = parser.parse_args()

    print("=" * 50)
    print("  ManaMind — démarrage du serveur")
    print(f"  URL : {url_for(args.port)}")
    print("  Ctrl+C pour arrêter")
    print("=" * 50)

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(args.port, 1.5), daemon=True).start()

    # Uvicorn est appelé en direct plutôt qu'en ligne de commande : sous Windows,
    # son CLI développe lui-même les jokers (« *.html » devient la liste des
    # fichiers) et refuse alors de démarrer.
    import uvicorn

    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    try:
        uvicorn.run(
            "server:app",
            host="0.0.0.0",
            port=args.port,
            reload=True,
            # Surveiller aussi les fichiers HTML et JSON
            reload_includes=["*.html", "*.json"],
        )
    except KeyboardInterrupt:
        print("\n[Start] Serveur arrêté.")


if __name__ == "__main__":
    main()
