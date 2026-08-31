# JRH One — Brand assets

Subí acá los archivos finales de marca. **No renombres** los archivos: el código los busca por estos nombres exactos.

## Estructura

```
static/brand/
├── README.md              ← esta guía
├── brand-icon.png         ← favicon, PWA, isotipo
├── brand-logo-light.png   ← logo horizontal (fondos claros)
├── brand-logo-dark.png    ← logo horizontal (fondos oscuros / sidebar)
└── email-footer.png       ← pie visual para emails transaccionales
```

## Qué subir en cada archivo

| Archivo | Uso en el producto | Recomendación |
|---------|-------------------|---------------|
| `brand-icon.png` | Favicon del navegador, icono PWA / “Agregar a inicio”, espacios chicos | PNG, fondo transparente si aplica. Cuadrado, mín. **512×512 px** (se escala hacia abajo). |
| `brand-logo-light.png` | Navbar, header, dashboard, auth (modo día / fondos claros) | PNG o JPG. Horizontal, altura ~**40–48 px** en pantalla (exportar ancho ~**600–800 px**). Texto/logo oscuro sobre fondo claro. |
| `brand-logo-dark.png` | Sidebar navy, dark mode, fondos `#0A1633` | Misma proporción que light. Versión **clara** (blanco/off-white) legible sobre navy. |
| `email-footer.png` | Firma visual al pie de mails (verificación, reset, aprobación) | PNG/JPG. Ancho máx. **~560 px**. Puede incluir logo + tagline “Gestión. Control. Resultados.” |

## Variables de entorno (opcional)

Si usás otros nombres o CDN, podés override sin mover archivos:

```bash
# Rutas relativas a static/ (sin barra inicial)
APP_BRAND_ICON=brand/brand-icon.png
APP_BRAND_LOGO_LIGHT=brand/brand-logo-light.png
APP_BRAND_LOGO_DARK=brand/brand-logo-dark.png
APP_BRAND_EMAIL_FOOTER=brand/email-footer.png

# URL pública para logo en emails (si no usás email-footer.png embebido)
# EMAIL_LOGO_URL=https://app.jrhone.com/static/brand/brand-logo-light.png
```

## Estado actual

Hasta que subas estos archivos, la app sigue usando los assets legacy en `static/images/` (`jrh-one-*.jpg`, `logo-*.png`) como fallback.

## Checklist antes de subir

- [ ] `brand-icon.png` — cuadrado, nítido en 32px y 512px
- [ ] `brand-logo-light.png` — probado sobre fondo `#F2F4F8`
- [ ] `brand-logo-dark.png` — probado sobre fondo `#0A1633`
- [ ] `email-footer.png` — legible en Gmail / Outlook mobile

## No subir acá

- Logos de **inmobiliarias** (van en `static/uploads/organizations/…`)
- Exports temporales, PSD, Figma — guardalos fuera del repo o en diseño interno
