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

Staff **Diagnosticar conexión** repeats the same listings GET and shows
only: base URL, endpoint, office ID, token configured yes/no, HTTP
status, and a sanitized body snippet (max 1000 chars).

`test_connection` query matches the known browser listings call, with
`pagesize=1`. JRH sends `Accept` + `Authorization: Bearer <token>` only.
It does not send browser cookies or `JSESSIONID`.

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

Beta strategy: `REMOTE_REFERENCE`, maximum **5** photos per property.

`photos[]` is the only automatic gallery source. Always keep
`primary=true` even if it is not first, then fill in array order.
Cover policy: explicit manual override → RedREMAX primary → first valid
media. Identity is the SHA-256 of the canonical HTTPS URL, not position.

Frontend list/detail/ACM/JRH cards use `get_property_media_url()`.
Remote `<img>` tags send `referrerpolicy="no-referrer"` so S3 hotlink
rules do not see the JRH host. Broken remotes become the clear
“Sin foto disponible” placeholder and do not delete `PropertyMedia`.
Brochure may fetch an allowlisted image once per PDF (in-memory cache).
`PublicListingMediaProvider` is disabled.

V1 strategy: `REMOTE_REFERENCE`.

Allowed host: `redremax-images.s3.amazonaws.com`.

Dedupe by SHA-256 of the canonical URL (scheme+host+path) **per property**.
Position is not identity. The same CDN URL may exist on two listings.

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

Brochure prefers a local file when one exists. For RedREMAX remote
references it may fetch an allowlisted HTTPS image once per PDF
(timeout, size cap, image content-type, host allowlist, in-memory cache).
A failed fetch skips that photo; the PDF still generates.

`get_property_media_for_generation()` returns authorized gallery items
(cover first, max 5) and does **not** send them to OpenAI.

## Conflicts

Excel/manual properties are not assumed to be the same listing.
Identity is `(organization_id, external_source, external_id)`.
Possible address duplicates stay in the existing conflict workflow
(Link / Create new). No auto-merge. Manual photos are not deleted.

## Demo / manual JSON import (temporary)

Until official authentication works, Staff can upload a RedREMAX listings
JSON export. This is **not** automatic sync.

Accepted envelopes:

- `{ "data": { "results": [...], "page", "pageSize", "totalItems", "totalPages" } }`
- `{ "results": [...] }`

Multiple page files may be uploaded together. Listings are merged by
`id` / `external_id` (first wins).

The upload is parsed in memory, stripped of `clients`, `clientsData`,
`documents`, and `privateNotes`, then passed through
`RedRemaxPropertyNormalizer` and the existing Property Sync Hub
(`sync_external_property`). Routes do not write `Property` rows directly.

Files that contain `Authorization`, `Bearer`, cookies, or `JSESSIONID`
are rejected. Those values are never stored.

Office check is the same as the live connector:
`listing.office == configured external_office_id`. Other offices are
skipped with a warning.

Preview writes nothing. Confirm uses a one-time token. A second confirm
of the same token fails. Importing the same JSON again is idempotent
(`organization_id` + `external_source=redremax` + `external_id`).

Imported properties keep `external_source=redremax` and store
`ingestion_method=manual_json_import` in metadata so a future official
connector can update the same rows.

Manual import only creates or updates. Listings missing from a later
file are **not** archived.

Excel/manual properties are never auto-merged by address. Possible
duplicates use the existing Link / Create-new conflict UI.

## What is still required for production

1. Official RedREMAX credentials and protocol documentation.
2. Implement that protocol in `RedRemaxOfficialAuthProvider`.
3. Confirm page size and any additional authorized endpoints.
4. Enable the scheduler only after official auth is stable.
5. Optional: a safe media cache if brochures must embed remote photos.
6. Optional: feature-ID dictionary adapter (do not show “Feature 35”).
