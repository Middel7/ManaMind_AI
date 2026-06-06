"""Extrair et mettre à jour le résumé Markdown du PPT de projet."""

from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

BASE_DIR = Path(__file__).resolve().parents[2]
PPTX_PATH = BASE_DIR / "docs" / "Projet_IA_MTG_Recommandation.pptx"
OUT_PATH = BASE_DIR / "docs" / "Projet_IA_MTG_Recommandation.md"


def extract_slide_texts(pptx_path: Path) -> list[list[str]]:
    """Extrait le texte de chaque diapositive du fichier PPTX."""
    if not pptx_path.exists():
        raise FileNotFoundError(f"Fichier PPTX introuvable: {pptx_path}")

    slide_texts: list[list[str]] = []
    with ZipFile(pptx_path, "r") as archive:
        slide_files = sorted(
            [name for name in archive.namelist() if name.startswith("ppt/slides/slide")]
        )
        for slide_file in slide_files:
            data = archive.read(slide_file).decode("utf-8", errors="ignore")
            texts = re.findall(r"<a:t>(.*?)</a:t>", data)
            cleaned = [text.strip() for text in texts if text.strip()]
            slide_texts.append(cleaned)
    return slide_texts


def build_markdown(slide_texts: list[list[str]]) -> str:
    """Construit un résumé Markdown à partir du texte des diapositives."""
    lines: list[str] = [
        "# Résumé du PPT - Projet IA MTG Recommandation",
        "",
        "Ce document est un résumé automatique de `docs/Projet_IA_MTG_Recommandation.pptx`.",
        "",
        "> Pour rafraîchir ce résumé : `python src/manamind/refresh_ppt_summary.py`",
        "",
    ]

    for idx, texts in enumerate(slide_texts, start=1):
        if not texts:
            continue
        lines.append(f"## Diapositive {idx}")
        seen: set[str] = set()
        for text in texts:
            normalized = " ".join(text.split())
            if normalized in seen:
                continue
            seen.add(normalized)
            lines.append(f"- {normalized}")
        lines.append("")

    lines.append("---")
    lines.append("\n*Résumé généré automatiquement depuis `docs/Projet_IA_MTG_Recommandation.pptx`.*")
    return "\n".join(lines)


def main() -> int:
    slide_texts = extract_slide_texts(PPTX_PATH)
    markdown = build_markdown(slide_texts)
    OUT_PATH.write_text(markdown, encoding="utf-8")
    print(f"Résumé mis à jour dans {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
