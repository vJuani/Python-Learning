# JRH One — Visual Brand Guide

Identity is the six official lockups. Do not invent a new logo or recolor the mark by hand.

Canonical files live in `static/brand/` and are exported from `static/images/1.png`–`6.png` by `scripts/export_brand_marks.py`.

## Colors

| Token | Hex | Usage |
|-------|-----|--------|
| Brand | `#156A4A` | Light primary |
| Brand hover | `#0E4F38` | Hover, PWA theme |
| Accent | `#23805A` | Light secondary accent |
| Night | `#080A0F` | Dark surfaces, email header |
| Violet | `#7C5CFF` | Dark / IA accent |
| Surface muted | `#F6F8F6` | Light app background |
| White | `#FFFFFF` | Cards |

CSS tokens: `static/css/tokens.css`. Product chrome uses `--color-brand`; organization color is `--org-accent` only.

## Marks

| Slot | File | Use |
|------|------|-----|
| Logo principal light | `logo-primary-light.png` | Login, landing, institutional, wide headers |
| Logo horizontal light | `logo-horizontal-light.png` | Navbar, topbar, compact desktop headers |
| Isotipo light | `isotype-light.png` | Footer / small IDs on light |
| App icon | `app-icon.png` + `static/icons/*` | Favicon, PWA, shortcuts |
| Logo dark green | `logo-dark-green.png` | Dark chrome with green continuity (sidebar, general dark) |
| Logo dark violet | `logo-dark-violet.png` | Login dark, JRH IA, tech dark |

Collapsed sidebar uses the **white isotype** (`isotype-dark-green` / `isotype-dark-violet`) so the mark stays legible on the green rail.

## Template API

All product UI logos go through `templates/_brand_logo.html` and `brand_marks` from `modules/branding.py`.

Set `brand_slot`:

- `login-primary` / `login-panel`
- `navbar`
- `sidebar-icon` / `sidebar-lockup`
- `auth-panel`
- `footer`
- `icon`

CSS in `static/css/brand-logo.css` picks light vs dark and green vs violet from `data-theme` and page body classes (`is-login-page`, `is-jrh-ask-page`, `is-home-v2`).

Do not hardcode `static/brand/...` in extra templates.

## Environment

```
APP_BRAND_NAME=JRH One
APP_DOMAIN=jrhone.com
APP_BASE_URL=https://app.jrhone.com
APP_BRAND_MARK_PRIMARY_LIGHT=brand/logo-primary-light.png
APP_BRAND_CHROME_FAVICON=icons/icon-192.png
```

## Emails

Header band is night (`#080A0F`) so the dark lockup stays visible. Logo URL: `get_brand_logo_dark_rel()` (green-accent dark lockup).

## Legacy

`static/images/jrh-one-*.jpg` and `logo-*.png` are unused by the product UI. Keep them until a dedicated cleanup; PDFs resolve via `resolve_brand_logo_path()` which prefers the new marks.
