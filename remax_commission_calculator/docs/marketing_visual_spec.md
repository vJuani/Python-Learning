# JRH Marketing Visual Spec

Canonical direction of art for JRH Creative AI.

The approved Story is a **style reference**, not a pixel template.
Property photos, the Agent portrait and all facts come from live listing data.

- Spec id: `JRH_MARKETING_VISUAL_SPEC`
- Code: `modules/marketing_visual_spec.py`
- Approved file: `marketing_style_references/jrh-approved-story.jpg`
- That file is never shown in the Agent UI.

## What the approved reference teaches

- Real-estate photography is the hero (about 40–60% of attention).
- Premium editorial composition, not a SaaS card.
- Deep JRH navy, electric blue accent, clean white.
- Large, sophisticated type with a clear hierarchy.
- Very few words. Commercial facts are obvious.
- One or two secondary photos, chosen with intent.
- The Agent is a professional cutout integrated into the composition (about 20–35% when requested).
- Price, address, facts and a simple CTA are readable.
- Vertical 9:16 thinking (Instagram Story / WhatsApp Status).
- Balance between photo, type and space.
- Agency polish. Not a dashboard template.

## Rejected defects

Empty canvases, microscopic type, long paragraphs, giant unused blue shapes,
mechanical thumbnail grids, duplicated logos, duplicated or tiny Agents,
HTML buttons, dashboard cards, clipped text, invented listings or faces.

## Visual hierarchy

1. Property hero photo  
2. Headline / address  
3. Secondary photos  
4. Price  
5. Key facts  
6. Agent  
7. CTA  
8. Branding  

Decoration never outranks the property.

## Pipeline

1. **MarketingArtDirector** — unique brief and composition (`editorial_navy`, `white_architectural`, `photo_led_luxury`). Same quality, different structure.
2. **MarketingImageProvider** — optional visual treatment. Receives labeled references. Does not write facts or draw the JRH logo.
3. **MarketingFinalCompositor** — pastes real PropertyMedia, the real Agent cutout from `get_agent_presentation_asset` / ACM, one official wordmark, and exact facts.
4. **MarketingQualityValidator** — rejects pieces below the approved level. One automatic retry, then `failed_quality`.

## Multi-image input

When the image provider accepts references:

| Role | Meaning |
|---|---|
| A. `style` | Approved JRH look. Polish and hierarchy only. Do not copy its property, person, text or layout. |
| B. `property_hero` | The actual listing hero. |
| C. `property_extra` | More photos of the **same** listing. |
| D. `agent` | The real Agent. Identity must stay exact. |

## Facts the compositor owns

Address, locality, price, rooms, bedrooms, bathrooms, surface, Agent name and contact.

Default copy:

```
Santamarina 1335
Victoria · Buenos Aires
USD 90.000
2 ambientes · 1 dormitorio · 1 baño · 42 m²
Consultame
```

Optional creative line: 3–8 words. No paragraphs.

## Agent identity

`Property.agent` → `get_agent_branding` / `get_agent_presentation_asset` → the same professional file ACM and the brochure already use.

No generated lookalike. No avatar. No initials. If the user asks for no photo, omit the portrait.

## Safe areas

Keep important type inside the Story / Status inset (`SAFE_INSET` in the spec module) so Instagram and WhatsApp do not clip it.

## Quality bar

The approved reference is the **minimum** visual level.
If a piece is clearly below that bar, do not mark it completed.
Show “No me convenció esta propuesta” and regenerate.
