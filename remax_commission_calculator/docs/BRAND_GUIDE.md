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

| Asset | Path | Use |
|-------|------|-----|
| Horizontal | `static/images/jrh-one-logo-horizontal.jpg` | Sidebar, header |
| Full | `static/images/jrh-one-logo-full.jpg` | Auth panel, emails |
| Icon | `static/images/jrh-one-icon.jpg` | Favicon, PWA |

Override via env: `APP_BRAND_LOGO_HORIZONTAL`, `APP_BRAND_LOGO_FULL`, `APP_BRAND_LOGO_ICON`.

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
