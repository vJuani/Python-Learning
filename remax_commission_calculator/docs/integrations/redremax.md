# RedREMAX → JRH One (Property Sync)

Read-only connector for office listings. Official authentication is **pending**.
The architecture is ready; a connector class existing does **not** mean Connected.

## Known endpoint

Base URL (override with `REDREMAX_API_BASE_URL`):

`https://api-ar.redremax.com`

Observed listings path:

`GET /listings/api/listings`

Organization sync always sends `byoffice=<configured external_office_id>`.
The office ID is stored per Organization. Do not hardcode it.

Do not send `agent=` on a full office sync. That filter is optional and only
for explicit one-agent queries.

## Environment variable names

Names only. Never commit values.

- `REDREMAX_API_BASE_URL`
- `REDREMAX_ACCESS_TOKEN`
- `REDREMAX_HTTP_TIMEOUT_SECONDS`

`REDREMAX_ACCESS_TOKEN` is read by `ConfiguredRedRemaxTokenProvider`.
Put the raw credential only (`eyJ...`). Do **not** include the word `Bearer`;
the client adds `Authorization: Bearer <token>` itself.

That provider is **NON-PRODUCTION** and is not official RedREMAX auth.
Do not paste a browser Bearer into the UI, the database, or the repository.

## Authentication

`RedRemaxAuthProvider.get_access_token()` is the only hook the client uses.

- `ConfiguredRedRemaxTokenProvider`: local/manual development only.
- `RedRemaxOfficialAuthProvider`: placeholder. Implement the official protocol
  (API token, OAuth, or service account) behind this same interface when
  RedREMAX provides documentation and authorized credentials.

Until official auth works:

- no scheduler
- no automatic sync when saving the office ID
- Staff must Test connection → Dry run → Sync now

UI states: Not configured, Authentication pending, Connected, Auth expired, Error.

A `401`/`403` fails the run with a safe Staff message. It does **not**
archive or deactivate properties. Logs go to stdout (Railway) and never
include the token, `Authorization`, cookies, or response headers.

## Pagination

The API returns `data.results`, `data.page`, `data.pageSize`,
`data.totalItems`, `data.totalPages`.

The connector walks `page = 1 … totalPages`. `pageSize` is configurable and
capped. `totalItems` is observability only (`source_total` on the sync run).

If a later page fails, already-fetched listings may be processed, but
**nothing is archived from absence**.

## Office scoping

Each `OrganizationPropertyIntegration` stores `external_office_id`.
Every request includes `byoffice`. Each result is checked:

`payload.office == configured office`

Mismatch → skip + security warning. Never import another office’s listing.

## Mapping

| RedREMAX | JRH |
| --- | --- |
| `id` | `external_id` (`external_source=redremax`) |
| `associate` | `external_agent_id` via `ExternalAgentMapping` |
| `office` | metadata + scope check |
| `mlsid`, `qrid`, `associateQrid` | sanitized metadata |
| `title`, `description` | stored as-is (no AI rewrite) |
| `type=sale` | `listing_purpose=sale` (UI: Venta) |
| `propertyType` | `REDREMAX_PROPERTY_TYPE_MAP` (unknown → `other` + warning) |
| `price.value` / `price.currency` | `listing_price` / `listing_currency` |
| `price.exposure` | metadata only |
| `location[0]`, `location[1]` | **longitude**, **latitude** (do not invert) |
| `location_source` | `external_redremax` |
| `dimensions.covered` | `covered_m2` |
| `dimensions.land` | `total_m2` only when type is `land` |
| `dimensions.totalBuilt` | metadata only (not `total_m2`) |
| `totalRooms` | `rooms` (never `len(rooms)` when `rooms` is a list) |
| `status=active` | `commercial_status=available` |
| `photos[].cdn` | remote `PropertyMedia` (`original_url`, hash of canonical URL) |
| `photos[].primary` | cover |

Unknown operation/type: keep original + warning. The listing is not dropped.

## Photos

V1 strategy: `REMOTE_REFERENCE`.

Allowed host: `redremax-images.s3.amazonaws.com`.

Dedupe by SHA-256 of the canonical URL (scheme+host+path). Position is not identity.

Signed S3 blueprint URLs (`X-Amz-…`) expire. They are **not** stored as
permanent media. V1 only records that blueprints exist.

Documents, clients, `clientsData`, and `privateNotes` are ignored.
Requests use `withClients=false`. Those objects must never be logged or
turned into Contacts / public `PropertyMedia`.

Commission seller/buyer may stay in sanitized metadata. They are not wired
to Operations, Billing, or agent current account.

## Price exposure

`is_external_price_publicly_usable()` is **false** in V1.
Internal Property display is not blocked.
Do not treat `price.exposure` as permission for brochure, social, or portals
until RedREMAX documents the field.

`priceHistory` is stored in `external_property_price_history` as a RedREMAX
snapshot. It does not overwrite JRH FX.

## Brochure and content

The current PDF engine uses local files only. Remote RedREMAX photos are
not downloaded on each brochure request.

`get_property_media_for_generation()` returns authorized gallery items and
does **not** send them to OpenAI.

## Conflicts

Excel/manual properties are not assumed to be the same listing.
Identity is `(organization_id, external_source, external_id)`.
Possible address duplicates stay in the existing conflict workflow
(Link / Create new). No auto-merge. Manual photos are not deleted.

## What is still required for production

1. Official RedREMAX credentials and protocol documentation.
2. Implement that protocol in `RedRemaxOfficialAuthProvider`.
3. Confirm page size and any additional authorized endpoints.
4. Enable the scheduler only after official auth is stable.
5. Optional: a safe media cache if brochures must embed remote photos.
6. Optional: feature-ID dictionary adapter (do not show “Feature 35”).
