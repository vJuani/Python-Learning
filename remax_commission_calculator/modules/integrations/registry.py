"""
Adapter registry.
"""

from __future__ import annotations

from modules.database.organization_integrations_repository import (
    PROVIDER_CSV_UPLOAD,
    PROVIDER_STUB_FIXTURE,
)
from modules.integrations.providers.csv_upload import (
    CsvUploadAdapter,
)
from modules.integrations.providers.stub_fixture import (
    StubFixtureAdapter,
)


_ADAPTERS = {
    PROVIDER_STUB_FIXTURE: StubFixtureAdapter(),
    PROVIDER_CSV_UPLOAD: CsvUploadAdapter(),
}


def get_adapter(provider):
    adapter = _ADAPTERS.get(provider)

    if adapter is None:
        raise ValueError(f"unsupported_provider:{provider}")

    return adapter
