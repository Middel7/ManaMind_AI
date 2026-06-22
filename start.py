#!/usr/bin/env python3
"""
start.py — Lance ManaMind avec rechargement automatique.

Usage :
    python start.py
    python start.py --no-browser   # ne pas ouvrir le navigateur
"""
import argparse
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://localhost:8080"


def open_browser(delay: float = 1.5) -> None:
    time.sleep(delay)
    webbrowser.open(URL)
    print(f"[Start] Navigateur ouvert sur {URL}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lance le serveur ManaMind")
    parser.add_argument("--no-browser", action="store_true", help="Ne pas ouvrir le navigateur")
    parser.add_argument("--port", type=int, default=8080, help="Port (défaut : 8080)")
    args = parser.parse_args()

    print("=" * 50)
    print("  ManaMind — démarrage du serveur")
    print(f"  URL : http://localhost:{args.port}")
    print("  Ctrl+C pour arrêter")
    print("=" * 50)

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(1.5,), daemon=True).start()

    cmd = [
        sys.executable, "-m", "uvicorn",
        "server:app",
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--reload",
        # Surveiller aussi les fichiers HTML et JSON
        "--reload-include", "*.html",
        "--reload-include", "*.json",
    ]

    try:
        subprocess.run(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        print("\n[Start] Serveur arrêté.")


if __name__ == "__main__":
    main()
