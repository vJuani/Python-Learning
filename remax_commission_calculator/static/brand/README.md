# JRH One — Brand assets

Official lockups. Do not invent replacements. Re-export with `python scripts/export_brand_marks.py` after updating the numbered sources in `static/images/`.

## Canonical files

```
static/brand/
├── logo-primary-light.png          ← Imagen 1 (claim)
├── logo-horizontal-light.png       ← Imagen 2 (navbar)
├── isotype-light.png               ← Imagen 3
├── app-icon.png                    ← Imagen 4
├── logo-dark-green.png             ← Imagen 5
├── logo-dark-violet.png            ← Imagen 6
├── logo-horizontal-dark-green.png  ← alias of 5 for compact dark chrome
├── logo-horizontal-dark-violet.png ← alias of 6
├── isotype-dark-green.png          ← white isotype for dark/green rail
├── isotype-dark-violet.png         ← white isotype for IA/dark violet
├── brand-logo-light.png            ← compatibility alias
├── brand-logo-dark.png             ← compatibility alias
├── brand-icon.png                  ← compatibility alias of app-icon
├── login-header-lockup.png         ← compatibility alias of primary light
└── email-footer.png
```

PWA / favicon derivatives: `static/icons/icon-192.png`, `icon-512.png`, `apple-touch-icon.png`, `icon-maskable-512.png`.

## Product mapping

| Context | Mark |
|---------|------|
| Login light / landing | `logo-primary-light.png` |
| Login dark / JRH IA dark | `logo-dark-violet.png` |
| Navbar / topbar light | `logo-horizontal-light.png` |
| Navbar dark general | `logo-dark-green.png` |
| Sidebar expanded | dark lockup (green, or violet on IA dark) |
| Sidebar collapsed | white isotype |
| Favicon / PWA | app icon derivatives in `static/icons/` |

All UI usage goes through `templates/_brand_logo.html` and `modules/branding.py` (`BRAND_MARKS`).

## Optional env overrides

```bash
APP_BRAND_MARK_PRIMARY_LIGHT=brand/logo-primary-light.png
APP_BRAND_MARK_HORIZONTAL_LIGHT=brand/logo-horizontal-light.png
APP_BRAND_CHROME_FAVICON=icons/icon-192.png
APP_BRAND_EMAIL_FOOTER=brand/email-footer.png
```

## Do not put here

- Organization / listing logos (`static/uploads/organizations/…`)
- Source PSD/Figma files
