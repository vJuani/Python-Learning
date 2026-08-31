# JRH One — Visual Brand Guide (brief)

Product identity **option 2**: professional SaaS, navy base, electric blue accents.

## Colors

| Token | Hex | Usage |
|-------|-----|--------|
| Navy | `#0A1633` | Sidebar, headers, primary text |
| Navy soft | `#111F3D` | Dark surfaces, email header |
| Electric blue | `#0D47FF` | Primary buttons, links, KPI accent |
| Steel | `#33415C` | Secondary text |
| Surface muted | `#F2F4F8` | App background |
| White | `#FFFFFF` | Cards, panels |

CSS variables live in `static/css/style.css` (`:root`) and overrides in `static/css/jrh-one.css`.

## Typography

- **Font:** Poppins (Google Fonts)
- **Weights:** 300–700
- Headings: 600–700, navy
- Body / labels: 400–500, steel for secondary

## Logo assets

Canonical folder: **`static/brand/`** — see `static/brand/README.md`.

| File | Purpose |
|------|---------|
| `brand-icon.png` | Favicon, PWA icon |
| `brand-logo-light.png` | Horizontal logo on light backgrounds |
| `brand-logo-dark.png` | Horizontal logo on navy / dark surfaces |
| `email-footer.png` | Email signature footer image |

Legacy fallbacks remain in `static/images/` until brand files are uploaded.

Override via env: `APP_BRAND_ICON`, `APP_BRAND_LOGO_LIGHT`, `APP_BRAND_LOGO_DARK`, `APP_BRAND_EMAIL_FOOTER`.

| Asset | Path | Use |
|-------|------|-----|
| Horizontal (legacy) | `static/images/jrh-one-logo-horizontal.jpg` | Sidebar, header |
| Full (legacy) | `static/images/jrh-one-logo-full.jpg` | Auth panel, emails |
| Icon (legacy) | `static/images/jrh-one-icon.jpg` | Favicon, PWA |

Template partial: `templates/_brand_logo.html` (`logo_variant`: horizontal | full | icon).

## Tagline

- ES: **Gestión. Control. Resultados.**
- EN: **Manage. Control. Deliver.**

Configured in i18n (`app_slogan`) and email copy.

## Environment

```
APP_BRAND_NAME=JRH One
APP_DOMAIN=jrhone.com
APP_BASE_URL=https://app.jrhone.com
```

Central module: `modules/branding.py`.

## Emails

- Base layout: `templates/email/base.html` (navy header band, Poppins, blue CTA)
- Transactional templates extend base and use inline styles for client compatibility

## Pending (second pass)

- Full dark-mode audit per module
- Illustration / icon set for empty states
- Landing page marketing site
- SVG logo exports (current assets are JPG)
- Replace `cc-theme` / `cc-rail` localStorage keys (would reset user prefs)
