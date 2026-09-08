# JRH AI mascot assets

Official transparent PNG/WebP only. Do not generate another robot,
do not use emoji, and do not crop the mascot from UI screenshots.

Drop files here. Templates resolve the first existing file with
`url_for('static', ...)` and the project's normal file-mtime version.

Supported names:

- `jrh-bot-hero.png` / `.webp`
- `jrh-bot-avatar.png` / `.webp`
- `jrh-bot-floating.png` / `.webp`

A single master is enough. Any of these is reused for hero, avatar
and floating until the other sizes exist:

- `jrh-bot.png` / `.webp`
- `jrh-bot-hero.png` / `.webp`

Filenames must stay lowercase for Linux/Railway.

SVG placeholders remain only as a last-resort fallback so the UI
never shows a broken image icon.
