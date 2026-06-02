#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import sys
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path
from wsgiref.simple_server import make_server
from wsgiref.util import request_uri

ROOT = Path(__file__).resolve().parent
USER_DECKLIST_DIR = ROOT / "data" / "User decklist"
USER_RECOMM_DIR = ROOT / "data" / "User recommendations"
USER_DECKLIST_DIR.mkdir(parents=True, exist_ok=True)
USER_RECOMM_DIR.mkdir(parents=True, exist_ok=True)
RECOMMEND_SCRIPT = ROOT / "src" / "manamind" / "recommend_deck_changes.py"
NEXT_PATTERN = re.compile(r"user_deck_(\d+)\.txt$")


def next_sequence_number() -> int:
    max_number = 0
    for deck_file in USER_DECKLIST_DIR.glob("user_deck_*.txt"):
        match = NEXT_PATTERN.match(deck_file.name)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return max_number + 1


def json_response(start_response, payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    start_response(f"{status} OK", [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body)))
    ])
    return [body]


def text_response(start_response, body, status=200, content_type="text/plain; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    start_response(f"{status} OK", [("Content-Type", content_type), ("Content-Length", str(len(body)))])
    return [body]


def serve_file(environ, start_response, file_path: Path):
    if not file_path.exists() or not file_path.is_file():
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"404 Not Found"]
    mime_type, _ = mimetypes.guess_type(str(file_path))
    content_type = mime_type or "application/octet-stream"
    data = file_path.read_bytes()
    start_response("200 OK", [("Content-Type", content_type), ("Content-Length", str(len(data)))])
    return [data]


def parse_multipart_form(environ):
    content_type = environ.get("CONTENT_TYPE", "")
    if not content_type.startswith("multipart/form-data"):
        return None

    content_length = environ.get("CONTENT_LENGTH")
    try:
        length = int(content_length) if content_length else 0
    except ValueError:
        length = 0

    body = environ["wsgi.input"].read(length) if length else environ["wsgi.input"].read()
    headers = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n".encode("utf-8")
    message = BytesParser(policy=default_policy).parsebytes(headers + b"\r\n" + body)
    return message


def handle_upload(environ, start_response):
    if environ["REQUEST_METHOD"].upper() != "POST":
        return json_response(start_response, {"error": "Méthode non autorisée"}, status=405)

    message = parse_multipart_form(environ)
    if message is None:
        return json_response(start_response, {"error": "Le contenu doit être multipart/form-data."}, status=400)

    deck_part = None
    for part in message.iter_parts():
        if part.get_content_disposition() == "form-data" and part.get_param("name", header="content-disposition") == "deckfile":
            deck_part = part
            break

    if deck_part is None:
        return json_response(start_response, {"error": "Aucun champ deckfile trouvé."}, status=400)

    filename = Path(deck_part.get_filename("uploaded_deck.txt")).name
    if not filename.lower().endswith(".txt"):
        return json_response(start_response, {"error": "Le fichier doit être un .txt."}, status=400)

    seq = next_sequence_number()
    saved_name = f"user_deck_{seq}.txt"
    recommendations_name = f"recommendations_example_{seq}.csv"
    saved_path = USER_DECKLIST_DIR / saved_name
    recommendations_path = USER_RECOMM_DIR / recommendations_name

    file_content = deck_part.get_payload(decode=True) or b""
    saved_path.write_bytes(file_content)

    try:
        completed = subprocess.run(
            [sys.executable, str(RECOMMEND_SCRIPT), "--input", str(saved_path), "--output", str(recommendations_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return json_response(start_response, {
            "error": "Échec de la génération des recommandations.",
            "details": exc.stderr or exc.stdout or str(exc),
        }, status=500)

    base_url = request_uri(environ).rsplit("/", 1)[0]
    return json_response(start_response, {
        "status": "ok",
        "deckFile": saved_name,
        "recommendationsFile": str(recommendations_path.relative_to(ROOT)).replace('\\', '/'),
        "sequence": seq,
    })


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if path == "/":
        return serve_file(environ, start_response, ROOT / "recommendations_view_slide16.html")
    if path == "/upload-deck":
        return handle_upload(environ, start_response)

    file_path = (ROOT / path.lstrip("/")).resolve()
    if ROOT not in file_path.parents and file_path != ROOT:
        start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"403 Forbidden"]
    return serve_file(environ, start_response, file_path)


def main():
    port = 8000
    print(f"Démarrage du serveur sur http://localhost:{port}")
    print(f"Upload TXT -> {USER_DECKLIST_DIR}")
    print(f"Recommandations CSV -> {USER_RECOMM_DIR}")
    with make_server("", port, application) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
