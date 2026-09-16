# JRH AI mascot assets

Official transparent PNG only. Do not generate another robot.

Resolver order (`modules/jrh_branding.py`):

1. `jrh-ia-{hero|avatar|launcher}-{light|dark}.png`
2. Legacy `jrh-bot-{hero|avatar|floating}.png`
3. SVG placeholders (`jrh-bot-*.svg`) — last-resort fallback only

`floating` maps to `launcher`.

Extract from:

`static/images/Imagen de Codex 15 sept 2026, 05_02_35 p.m..png`

with `scripts/extract_jrh_ia_assets.py`.
