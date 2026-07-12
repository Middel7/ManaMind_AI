"""Variables et utilitaires partagés entre tous les routers."""
from __future__ import annotations
import json as _json
import os as _os
from pathlib import Path
from fastapi.responses import Response
from slowapi import Limiter
from slowapi.util import get_remote_address

# ROOT pointe vers la racine du projet (4 niveaux au-dessus de _shared.py)
# src/manamind/routers/_shared.py → root
ROOT = Path(__file__).resolve().parents[3]
UPLOADS_DIR = ROOT / "uploads"
OUTPUTS_DIR = ROOT / "outputs"

_DEBUG = _os.environ.get("DEBUG", "").lower() in ("1", "true")
_SECURE_COOKIE = _os.environ.get("HTTPS_ENABLED", "").lower() in ("1", "true", "yes")

# Instance unique du rate-limiter — importée dans server.py pour éviter les doublons
limiter = Limiter(key_func=get_remote_address)


def _json_response(data: dict, status_code: int = 200) -> Response:
    """JSONResponse avec support UTF-8 complet (pas d'échappement ASCII)."""
    return Response(
        content=_json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        media_type="application/json; charset=utf-8",
    )
