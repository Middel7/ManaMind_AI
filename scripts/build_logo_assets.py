#!/usr/bin/env python3
"""Fabrique les images du logo servies par l'application.

Les originaux (assets/Manamind_*.png, 1254 px, plus d'un mégaoctet chacun)
ne sont jamais servis tels quels : ce script en tire des versions à la taille
où elles s'affichent. À relancer si le logo change.

  Manamind_Trans.png (fond transparent) → l'interface
    static/img/logo-emblem.webp   l'emblème seul, marque de la barre latérale
                                  et des pages publiques
    static/img/favicon-32.png     onglet du navigateur

  Manamind_Noir.png (fond noir) → ce qui s'affiche hors de l'interface
    static/img/apple-touch-icon.png, icon-192.png, icon-512.png
    static/img/icon-maskable-512.png   emblème dans la zone sûre de 80 %
    static/img/og-image.png            aperçu d'un lien partagé (1200 × 630)

Manamind_Blanc.png n'est pas utilisé : l'interface n'a pas de thème clair.

Usage :
    .venv\\Scripts\\python.exe scripts/build_logo_assets.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets"
OUT = ROOT / "static" / "img"

# Fond des icônes : le noir pur de Manamind_Noir.png. Le fond de l'app
# (--bg, #08080C) laissait voir le rectangle de l'original posé dessus.
BG = (0, 0, 0)


def _emblem_bottom(alpha: Image.Image) -> int:
    """Dernière ligne de l'emblème, au-dessus du nom.

    On descend depuis le milieu de l'image jusqu'à la première bande vide :
    c'est l'espace qui sépare le cadre du texte « ManaMind.eu ».
    """
    width, height = alpha.size
    pixels = alpha.load()
    for y in range(height // 2, height):
        if not any(pixels[x, y] > 40 for x in range(0, width, 2)):
            return y
    return height


def _emblem(img: Image.Image, alpha: Image.Image) -> Image.Image:
    """L'emblème seul, rogné au plus juste puis mis au carré."""
    bottom = _emblem_bottom(alpha)
    box = alpha.crop((0, 0, img.width, bottom)).getbbox()
    crop = img.crop(box)
    side = max(crop.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return square


def _on_bg(emblem: Image.Image, size: int, scale: float) -> Image.Image:
    """L'emblème centré sur le fond de l'app, occupant `scale` du côté."""
    canvas = Image.new("RGB", (size, size), BG)
    inner = round(size * scale)
    mark = emblem.resize((inner, inner), Image.LANCZOS)
    canvas.paste(mark, ((size - inner) // 2, (size - inner) // 2), mark)
    return canvas


def main() -> None:
    trans = Image.open(SRC / "Manamind_Trans.png").convert("RGBA")
    noir = Image.open(SRC / "Manamind_Noir.png").convert("RGBA")
    alpha = trans.getchannel("A")

    emblem = _emblem(trans, alpha)

    # Interface : 96 px pour une marque affichée à 32 px, net sur écran dense.
    emblem.resize((96, 96), Image.LANCZOS).save(OUT / "logo-emblem.webp", quality=90)
    emblem.resize((32, 32), Image.LANCZOS).save(OUT / "favicon-32.png", optimize=True)

    # Icônes : l'emblème du fond noir est le même dessin, mais ses bords
    # n'ont pas les franges de détourage de la version transparente. On le
    # recadre sur la zone de l'emblème transparent, au pixel près.
    box = alpha.crop((0, 0, trans.width, _emblem_bottom(alpha))).getbbox()
    noir_emblem = noir.crop(box)
    side = max(noir_emblem.size)
    square = Image.new("RGBA", (side, side), BG + (255,))
    square.paste(noir_emblem, ((side - noir_emblem.width) // 2, (side - noir_emblem.height) // 2))

    _on_bg(square, 180, 0.86).save(OUT / "apple-touch-icon.png", optimize=True)
    _on_bg(square, 192, 0.86).save(OUT / "icon-192.png", optimize=True)
    _on_bg(square, 512, 0.86).save(OUT / "icon-512.png", optimize=True)
    # Android rogne l'icône « maskable » en cercle ou en goutte : seul le
    # disque central de 80 % est sûr d'être visible.
    _on_bg(square, 512, 0.66).save(OUT / "icon-maskable-512.png", optimize=True)

    # Aperçu de lien : le logo complet, au format 1,91:1 des réseaux sociaux.
    og = Image.new("RGB", (1200, 630), BG)
    logo = noir.convert("RGB").crop(alpha.getbbox())
    height = 560
    logo = logo.resize((round(logo.width * height / logo.height), height), Image.LANCZOS)
    og.paste(logo, ((1200 - logo.width) // 2, (630 - height) // 2))
    og.save(OUT / "og-image.png", optimize=True)

    for name in ("logo-emblem.webp", "favicon-32.png", "apple-touch-icon.png",
                 "icon-192.png", "icon-512.png", "icon-maskable-512.png", "og-image.png"):
        path = OUT / name
        print(f"{path.stat().st_size / 1024:7.1f} Ko  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
