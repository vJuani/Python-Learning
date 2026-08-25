"""
CSV import bridge for organization integrations.

listing_price has no currency column on properties/listings.
MVP accepts only USD (no FX conversion). ARS requires a future
listing_currency (or equivalent) schema change.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from typing import Optional

from modules.database.csv_import_batches_repository import (
    create_csv_import_batch,
    delete_csv_import_batch,
    get_csv_import_batch,
)
from modules.database.organization_integrations_repository import (
    PROVIDER_CSV_UPLOAD,
    SCOPE_ORGANIZATION,
    STATUS_CONNECTED,
    create_organization_integration,
    find_organization_integration_by_provider,
    update_organization_integration_config,
)
from modules.integrations.matching import (
    match_agent_by_external_id,
    match_listing_by_external_id,
)
from modules.integrations.types import (
    ExternalAgent,
    ExternalProperty,
)
from modules.property_types import (
    LISTING_PURPOSES,
    PROPERTY_TYPES,
)
from modules.validators import JURISDICTIONS


REQUIRED_COLUMNS = (
    "agent_external_id",
    "agent_name",
    "property_external_id",
    "address",
    "jurisdiction",
    "url",
)

OPTIONAL_COLUMNS = (
    "agent_email",
    "status",
    "price",
    "currency",
    "property_type",
    "listing_purpose",
    "listing_provider",
)

ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

LISTING_STATUSES = (
    "active",
    "paused",
    "reserved",
    "negotiation",
    "sold",
    "inactive",
)

DEFAULT_LISTING_PROVIDER = "remax_web"
DEFAULT_LISTING_STATUS = "active"
DEFAULT_CURRENCY = "USD"
SUPPORTED_IMPORT_CURRENCIES = ("USD",)

MAX_CSV_ROWS = 5000

LISTING_PROVIDERS = (
    "remax_web",
    "organization_website",
    "zonaprop",
    "argenprop",
    "mercadolibre",
    "other",
)


@dataclass
class CsvIssue:
    severity: str  # error | warning | info
    code: str
    message: str
    row_number: Optional[int] = None


@dataclass
class ParsedCsvRow:
    row_number: int
    agent_external_id: str
    agent_name: str
    agent_email: Optional[str]
    property_external_id: str
    address: str
    jurisdiction: str
    url: str
    status: str
    price: Optional[float]
    currency: str
    property_type: Optional[str]
    listing_purpose: Optional[str]
    listing_provider: str


@dataclass
class CsvParseResult:
    rows: list[ParsedCsvRow] = field(default_factory=list)
    issues: list[CsvIssue] = field(default_factory=list)
    filename: Optional[str] = None

    @property
    def has_blocking_errors(self) -> bool:
        return any(
            issue.severity == "error"
            for issue in self.issues
        )


def _issue(severity, code, message, row_number=None):
    return CsvIssue(
        severity=severity,
        code=code,
        message=message,
        row_number=row_number,
    )


def _decode_csv_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError("csv_encoding_invalid")


def _normalize_header(value: str) -> str:
    return (value or "").strip().lower()


def _cell(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return str(value).strip()


def parse_csv_text(
    text: str,
    *,
    filename: Optional[str] = None,
    default_listing_provider: str = DEFAULT_LISTING_PROVIDER,
) -> CsvParseResult:
    result = CsvParseResult(filename=filename)

    if text is None or text.strip() == "":
        result.issues.append(
            _issue(
                "error",
                "csv_empty",
                "El archivo CSV está vacío.",
            )
        )
        return result

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        result.issues.append(
            _issue(
                "error",
                "csv_missing_header",
                "Falta la fila de encabezados.",
            )
        )
        return result

    headers = [
        _normalize_header(name)
        for name in reader.fieldnames
    ]

    if any(name == "" for name in headers):
        result.issues.append(
            _issue(
                "error",
                "csv_blank_header",
                "Hay columnas sin nombre en el encabezado.",
            )
        )
        return result

    header_set = set(headers)

    for required in REQUIRED_COLUMNS:
        if required not in header_set:
            result.issues.append(
                _issue(
                    "error",
                    "csv_missing_column",
                    f"Falta la columna obligatoria '{required}'.",
                )
            )

    unknown = [
        name
        for name in headers
        if name not in ALL_COLUMNS
    ]

    for name in unknown:
        result.issues.append(
            _issue(
                "warning",
                "csv_unknown_column",
                f"Columna ignorada: '{name}'.",
            )
        )

    if result.has_blocking_errors:
        return result

    agent_names = {}
    property_ids_seen = {}
    data_row_count = 0

    for index, raw_row in enumerate(reader, start=2):
        normalized = {
            _normalize_header(key): value
            for key, value in raw_row.items()
            if key is not None
        }

        # Skip fully empty rows
        if not any(
            _cell(normalized, col)
            for col in ALL_COLUMNS
            if col in normalized
        ):
            continue

        data_row_count += 1

        if data_row_count > MAX_CSV_ROWS:
            result.issues.append(
                _issue(
                    "error",
                    "csv_too_many_rows",
                    f"El CSV supera el límite de {MAX_CSV_ROWS} filas.",
                )
            )
            break

        agent_external_id = _cell(
            normalized,
            "agent_external_id",
        )
        agent_name = _cell(normalized, "agent_name")
        agent_email = _cell(normalized, "agent_email") or None
        property_external_id = _cell(
            normalized,
            "property_external_id",
        )
        address = _cell(normalized, "address")
        jurisdiction = _cell(
            normalized,
            "jurisdiction",
        ).upper()
        url = _cell(normalized, "url")
        status = (
            _cell(normalized, "status").lower()
            or DEFAULT_LISTING_STATUS
        )
        price_raw = _cell(normalized, "price")
        currency = (
            _cell(normalized, "currency").upper()
            or DEFAULT_CURRENCY
        )
        property_type = (
            _cell(normalized, "property_type").lower()
            or None
        )
        listing_purpose = (
            _cell(normalized, "listing_purpose").lower()
            or None
        )
        listing_provider = (
            _cell(normalized, "listing_provider").lower()
            or default_listing_provider
        )

        row_errors = []

        if not agent_external_id:
            row_errors.append(
                "agent_external_id es obligatorio."
            )
        if not agent_name:
            row_errors.append("agent_name es obligatorio.")
        if not property_external_id:
            row_errors.append(
                "property_external_id es obligatorio."
            )
        if not address:
            row_errors.append("address es obligatorio.")
        if not jurisdiction:
            row_errors.append(
                "jurisdiction es obligatorio."
            )
        elif jurisdiction not in JURISDICTIONS:
            row_errors.append(
                "jurisdiction debe ser CABA o PBA."
            )
        if not url:
            row_errors.append("url es obligatorio.")
        elif not (
            url.startswith("http://")
            or url.startswith("https://")
        ):
            row_errors.append(
                "url debe empezar con http:// o https://."
            )

        if status not in LISTING_STATUSES:
            row_errors.append(
                "status inválido "
                f"(valores: {', '.join(LISTING_STATUSES)})."
            )

        price = None
        if price_raw != "":
            try:
                price = float(price_raw.replace(",", ""))
                if price < 0:
                    row_errors.append(
                        "price debe ser mayor o igual a 0."
                    )
                    price = None
            except ValueError:
                row_errors.append(
                    "price debe ser un número válido."
                )

        if currency not in SUPPORTED_IMPORT_CURRENCIES:
            row_errors.append(
                "currency debe ser USD en este MVP "
                "(listing_price no tiene moneda en el schema)."
            )

        if (
            property_type is not None
            and property_type not in PROPERTY_TYPES
        ):
            row_errors.append(
                "property_type inválido."
            )

        if (
            listing_purpose is not None
            and listing_purpose not in LISTING_PURPOSES
        ):
            row_errors.append(
                "listing_purpose inválido."
            )

        if listing_provider not in LISTING_PROVIDERS:
            row_errors.append(
                "listing_provider inválido."
            )

        if property_external_id:
            previous = property_ids_seen.get(
                property_external_id
            )
            if previous is not None:
                row_errors.append(
                    "property_external_id duplicado "
                    f"en el CSV (también en fila {previous})."
                )
            else:
                property_ids_seen[property_external_id] = (
                    index
                )

        if agent_external_id and agent_name:
            existing_name = agent_names.get(
                agent_external_id
            )
            if (
                existing_name is not None
                and existing_name != agent_name
            ):
                row_errors.append(
                    "agent_external_id con nombres "
                    "inconsistentes en el CSV."
                )
            else:
                agent_names[agent_external_id] = agent_name

        for message in row_errors:
            result.issues.append(
                _issue(
                    "error",
                    "csv_row_invalid",
                    message,
                    row_number=index,
                )
            )

        if row_errors:
            continue

        if price_raw == "":
            result.issues.append(
                _issue(
                    "info",
                    "csv_price_missing",
                    "price vacío; se importará sin listing_price.",
                    row_number=index,
                )
            )

        result.rows.append(
            ParsedCsvRow(
                row_number=index,
                agent_external_id=agent_external_id,
                agent_name=agent_name,
                agent_email=agent_email,
                property_external_id=property_external_id,
                address=address,
                jurisdiction=jurisdiction,
                url=url,
                status=status,
                price=price,
                currency=currency,
                property_type=property_type,
                listing_purpose=listing_purpose,
                listing_provider=listing_provider,
            )
        )

    if (
        data_row_count == 0
        and not result.has_blocking_errors
    ):
        result.issues.append(
            _issue(
                "error",
                "csv_no_data_rows",
                "El CSV no tiene filas de datos.",
            )
        )

    return result


def parse_csv_bytes(
    raw: bytes,
    *,
    filename: Optional[str] = None,
    default_listing_provider: str = DEFAULT_LISTING_PROVIDER,
) -> CsvParseResult:
    try:
        text = _decode_csv_bytes(raw)
    except ValueError:
        result = CsvParseResult(filename=filename)
        result.issues.append(
            _issue(
                "error",
                "csv_encoding_invalid",
                "No se pudo leer el CSV como UTF-8.",
            )
        )
        return result

    return parse_csv_text(
        text,
        filename=filename,
        default_listing_provider=default_listing_provider,
    )


def rows_to_payload(rows: list[ParsedCsvRow]) -> dict:
    agents_by_id = {}
    properties = []

    for row in rows:
        if row.agent_external_id not in agents_by_id:
            agents_by_id[row.agent_external_id] = {
                "external_id": row.agent_external_id,
                "full_name": row.agent_name,
                "email": row.agent_email,
                "is_active": True,
            }

        properties.append(
            {
                "external_id": row.property_external_id,
                "agent_external_id": row.agent_external_id,
                "address": row.address,
                "jurisdiction": row.jurisdiction,
                "url": row.url,
                "listing_provider": row.listing_provider,
                "listing_status": row.status,
                "property_type": row.property_type,
                "listing_price": row.price,
                "listing_purpose": row.listing_purpose,
                "listing_currency": row.currency,
            }
        )

    return {
        "agents": list(agents_by_id.values()),
        "properties": properties,
    }


def payload_to_external(payload: dict) -> tuple[
    list[ExternalAgent],
    list[ExternalProperty],
]:
    agents = [
        ExternalAgent(
            external_id=item["external_id"],
            full_name=item["full_name"],
            email=item.get("email"),
            is_active=item.get("is_active", True),
        )
        for item in payload.get("agents", [])
    ]
    properties = [
        ExternalProperty(
            external_id=item["external_id"],
            agent_external_id=item["agent_external_id"],
            address=item["address"],
            jurisdiction=item["jurisdiction"],
            url=item.get("url"),
            listing_provider=item["listing_provider"],
            listing_status=item.get(
                "listing_status",
                DEFAULT_LISTING_STATUS,
            ),
            property_type=item.get("property_type"),
            listing_price=item.get("listing_price"),
            listing_purpose=item.get("listing_purpose"),
            listing_currency=item.get("listing_currency"),
            buyer_side_commission_percent=item.get(
                "buyer_side_commission_percent"
            ),
            seller_side_commission_percent=item.get(
                "seller_side_commission_percent"
            ),
        )
        for item in payload.get("properties", [])
    ]
    return agents, properties


def build_preview(
    organization_id,
    parse_result: CsvParseResult,
) -> dict:
    payload = rows_to_payload(parse_result.rows)
    agents_preview = []
    properties_preview = []
    issues = [
        asdict(issue) for issue in parse_result.issues
    ]

    agents_new = 0
    agents_update = 0
    properties_new = 0
    properties_update = 0
    listings_new = 0
    listings_update = 0

    listing_providers = set()

    for agent in payload["agents"]:
        matched = match_agent_by_external_id(
            organization_id,
            PROVIDER_CSV_UPLOAD,
            agent["external_id"],
        )
        action = "create" if matched is None else "update"

        if action == "create":
            agents_new += 1
        else:
            agents_update += 1
            if matched["name"] != agent["full_name"]:
                issues.append(
                    asdict(
                        _issue(
                            "warning",
                            "agent_name_will_update",
                            (
                                f"El agente {agent['external_id']} "
                                f"cambiará de nombre "
                                f"('{matched['name']}' → "
                                f"'{agent['full_name']}')."
                            ),
                        )
                    )
                )

        agents_preview.append(
            {
                "agent_external_id": agent["external_id"],
                "agent_name": agent["full_name"],
                "agent_email": agent.get("email"),
                "match": (
                    None
                    if matched is None
                    else {
                        "id": matched["id"],
                        "name": matched["name"],
                    }
                ),
                "action": action,
            }
        )

    agent_name_by_external = {
        item["agent_external_id"]: item["agent_name"]
        for item in agents_preview
    }

    for prop in payload["properties"]:
        listing_providers.add(prop["listing_provider"])
        matched = match_listing_by_external_id(
            organization_id,
            prop["listing_provider"],
            prop["external_id"],
        )
        action = "create" if matched is None else "update"

        if action == "create":
            properties_new += 1
            listings_new += 1
        else:
            properties_update += 1
            listings_update += 1

        properties_preview.append(
            {
                "property_external_id": prop["external_id"],
                "address": prop["address"],
                "agent_external_id": prop[
                    "agent_external_id"
                ],
                "agent_name": agent_name_by_external.get(
                    prop["agent_external_id"]
                ),
                "url": prop["url"],
                "status": prop["listing_status"],
                "price": prop.get("listing_price"),
                "currency": DEFAULT_CURRENCY,
                "property_type": prop.get("property_type"),
                "listing_purpose": prop.get(
                    "listing_purpose"
                ),
                "listing_provider": prop[
                    "listing_provider"
                ],
                "jurisdiction": prop["jurisdiction"],
                "match": (
                    None
                    if matched is None
                    else {
                        "listing_id": matched["id"],
                        "property_id": matched[
                            "property_id"
                        ],
                    }
                ),
                "action": action,
            }
        )

    blocking_errors = [
        issue
        for issue in issues
        if issue["severity"] == "error"
    ]
    warnings = [
        issue
        for issue in issues
        if issue["severity"] == "warning"
    ]

    return {
        "filename": parse_result.filename,
        "can_confirm": len(blocking_errors) == 0
        and len(parse_result.rows) > 0,
        "default_listing_provider": (
            DEFAULT_LISTING_PROVIDER
        ),
        "listing_providers": sorted(listing_providers),
        "integration_provider": PROVIDER_CSV_UPLOAD,
        "currency_policy": (
            "USD-only: properties.listing_price has no "
            "currency column; ARS requires schema change."
        ),
        "summary": {
            "rows_valid": len(parse_result.rows),
            "agents_total": len(agents_preview),
            "agents_new": agents_new,
            "agents_update": agents_update,
            "properties_new": properties_new,
            "properties_update": properties_update,
            "listings_new": listings_new,
            "listings_update": listings_update,
            "errors": len(blocking_errors),
            "warnings": len(warnings),
        },
        "agents": agents_preview,
        "properties": properties_preview,
        "issues": issues,
        "payload": payload,
    }


def stage_csv_import(
    organization_id,
    parse_result: CsvParseResult,
):
    preview = build_preview(organization_id, parse_result)
    payload = preview.pop("payload")

    batch = create_csv_import_batch(
        organization_id,
        filename=parse_result.filename,
        payload=payload,
        preview=preview,
    )

    return batch


def get_or_create_csv_upload_integration(organization_id):
    existing = find_organization_integration_by_provider(
        organization_id,
        PROVIDER_CSV_UPLOAD,
        scope_type=SCOPE_ORGANIZATION,
    )

    if existing is not None:
        return existing

    return create_organization_integration(
        organization_id,
        PROVIDER_CSV_UPLOAD,
        SCOPE_ORGANIZATION,
        status=STATUS_CONNECTED,
        external_office_id="csv-upload",
        config={
            "deactivate_missing_listings": False,
            "default_listing_provider": (
                DEFAULT_LISTING_PROVIDER
            ),
        },
    )


def prepare_csv_integration_for_batch(
    organization_id,
    batch_id,
):
    batch = get_csv_import_batch(batch_id, organization_id)

    if batch is None:
        raise ValueError("csv_batch_not_found")

    preview = batch["preview"]

    if not preview.get("can_confirm"):
        raise ValueError("csv_batch_has_blockers")

    integration = get_or_create_csv_upload_integration(
        organization_id
    )

    config = dict(integration.get("config") or {})
    config.update(
        {
            "batch_id": batch_id,
            "deactivate_missing_listings": False,
            "default_listing_provider": (
                DEFAULT_LISTING_PROVIDER
            ),
            "original_filename": batch.get("filename"),
            "payload": batch["payload"],
        }
    )

    return update_organization_integration_config(
        integration["id"],
        organization_id,
        config,
    )


def clear_csv_batch_from_integration(
    organization_id,
    integration_id,
    batch_id,
):
    from modules.database.organization_integrations_repository import (
        get_organization_integration,
    )

    integration = get_organization_integration(
        integration_id,
        organization_id,
    )

    if integration is None:
        delete_csv_import_batch(batch_id, organization_id)
        return None

    config = dict(integration.get("config") or {})
    config.pop("batch_id", None)
    config.pop("payload", None)
    config["deactivate_missing_listings"] = False
    config["last_import_filename"] = config.pop(
        "original_filename",
        None,
    )

    updated = update_organization_integration_config(
        integration_id,
        organization_id,
        config,
    )
    delete_csv_import_batch(batch_id, organization_id)
    return updated
