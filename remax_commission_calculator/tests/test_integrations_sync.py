"""
Tests for integration sync infrastructure (stub fixtures, no network).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(
    Path(_TEST_TMP.name) / "test_integrations.db"
)

from modules.config import apply_config
from modules.database import (
    add_agent,
    add_organization,
    create_tables,
    get_agents,
    get_properties,
    list_property_external_listings,
)
from modules.database.agents_repository import (
    find_agent_by_external_id,
)
from modules.database.organization_integrations_repository import (
    SCOPE_AGENT,
    SCOPE_ORGANIZATION,
)
from modules.integrations import (
    create_stub_integration,
    run_integration_sync,
)
from modules.integrations.matching import (
    match_agent_by_external_id,
)
from modules.integrations.providers import (
    FIXTURE_DATA_HOUSE,
    FIXTURE_INDEPENDENT_AGENT,
)
from web_app import app


class IntegrationSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config["TESTING"] = True
        create_tables()

        cls.org_a = add_organization("Org A Sync")
        cls.org_b = add_organization("Org B Sync")

    def test_organization_scope_imports_agents_and_properties(self):
        integration = create_stub_integration(
            self.org_a,
            scope_type=SCOPE_ORGANIZATION,
            fixture_key=FIXTURE_DATA_HOUSE,
            external_office_id="data-house-demo",
        )

        result = run_integration_sync(
            integration["id"],
            self.org_a,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.agents_created, 3)
        self.assertEqual(result.properties_created, 4)
        self.assertEqual(result.listings_created, 4)

        agents = get_agents(self.org_a)
        synced = [
            agent
            for agent in agents
            if agent.get("external_provider") == "stub_fixture"
        ]
        self.assertEqual(len(synced), 3)

        nieves = find_agent_by_external_id(
            self.org_a,
            "stub_fixture",
            "DH-AGENT-1",
        )
        self.assertIsNotNone(nieves)
        self.assertEqual(nieves["name"], "Nieves Achard")

        properties = get_properties(
            self.org_a,
            include_all_statuses=True,
        )
        self.assertEqual(len(properties), 4)

        for property_data in properties:
            self.assertEqual(property_data["status"], "approved")
            listings = list_property_external_listings(
                property_data["id"],
                self.org_a,
            )
            self.assertEqual(len(listings), 1)
            self.assertEqual(listings[0]["provider"], "remax_web")
            self.assertEqual(listings[0]["status"], "active")

    def test_second_sync_is_idempotent(self):
        org = add_organization("Org Idempotent")
        integration = create_stub_integration(
            org,
            scope_type=SCOPE_ORGANIZATION,
            fixture_key=FIXTURE_DATA_HOUSE,
            external_office_id="data-house-idemp",
        )

        first = run_integration_sync(integration["id"], org)
        second = run_integration_sync(integration["id"], org)

        self.assertEqual(first.agents_created, 3)
        self.assertEqual(first.properties_created, 4)
        self.assertEqual(second.agents_created, 0)
        self.assertEqual(second.properties_created, 0)
        self.assertEqual(second.listings_created, 0)
        self.assertEqual(second.agents_updated, 3)
        self.assertEqual(second.properties_updated, 4)
        self.assertEqual(second.listings_updated, 4)

        agents = [
            agent
            for agent in get_agents(org)
            if agent.get("external_id")
        ]
        self.assertEqual(len(agents), 3)

        properties = get_properties(
            org,
            include_all_statuses=True,
        )
        self.assertEqual(len(properties), 4)

    def test_agent_scope_imports_only_anchor_properties(self):
        org = add_organization("Org Solo Agent")
        local_agent = add_agent(
            "Solo Agent",
            "Alto",
            org,
        )

        integration = create_stub_integration(
            org,
            scope_type=SCOPE_AGENT,
            fixture_key=FIXTURE_INDEPENDENT_AGENT,
            external_office_id="agent-solo-demo",
            agent_id=local_agent,
        )

        result = run_integration_sync(integration["id"], org)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.agents_created, 0)
        self.assertEqual(result.properties_created, 2)
        self.assertEqual(result.listings_created, 2)

        agents = get_agents(org)
        self.assertEqual(len(agents), 1)

        properties = get_properties(
            org,
            include_all_statuses=True,
        )
        self.assertEqual(len(properties), 2)

        for property_data in properties:
            self.assertEqual(
                property_data["agent_id"],
                local_agent,
            )

        second = run_integration_sync(integration["id"], org)
        self.assertEqual(second.properties_created, 0)
        self.assertEqual(second.listings_created, 0)
        self.assertEqual(second.properties_updated, 2)

    def test_tenant_isolation_on_sync(self):
        org_one = add_organization("Tenant One Sync")
        org_two = add_organization("Tenant Two Sync")

        integration_one = create_stub_integration(
            org_one,
            scope_type=SCOPE_ORGANIZATION,
            fixture_key=FIXTURE_DATA_HOUSE,
            external_office_id="office-one",
        )
        run_integration_sync(integration_one["id"], org_one)

        with self.assertRaises(Exception):
            run_integration_sync(
                integration_one["id"],
                org_two,
            )

        self.assertEqual(
            len(
                [
                    agent
                    for agent in get_agents(org_two)
                    if agent.get("external_id")
                ]
            ),
            0,
        )
        self.assertEqual(
            len(
                get_properties(
                    org_two,
                    include_all_statuses=True,
                )
            ),
            0,
        )

        # Same external_id can exist in another org after its own sync
        integration_two = create_stub_integration(
            org_two,
            scope_type=SCOPE_ORGANIZATION,
            fixture_key=FIXTURE_DATA_HOUSE,
            external_office_id="office-two",
        )
        run_integration_sync(integration_two["id"], org_two)

        agent_one = find_agent_by_external_id(
            org_one,
            "stub_fixture",
            "DH-AGENT-1",
        )
        agent_two = find_agent_by_external_id(
            org_two,
            "stub_fixture",
            "DH-AGENT-1",
        )
        self.assertIsNotNone(agent_one)
        self.assertIsNotNone(agent_two)
        self.assertNotEqual(agent_one["id"], agent_two["id"])

    def test_matching_never_uses_name_alone(self):
        org = add_organization("Org Match")
        add_agent("Nieves Achard", "Alto", org)

        matched = match_agent_by_external_id(
            org,
            "stub_fixture",
            "DH-AGENT-1",
        )
        self.assertIsNone(matched)

    def test_missing_remote_listing_becomes_inactive(self):
        org = add_organization("Org Deactivate")
        integration = create_stub_integration(
            org,
            scope_type=SCOPE_ORGANIZATION,
            fixture_key=FIXTURE_DATA_HOUSE,
            external_office_id="office-deact",
        )
        run_integration_sync(integration["id"], org)

        # Manually shrink fixture behavior by running agent-scope isn't right.
        # Simulate: mark by calling deactivate path via second sync with
        # a custom seen set — instead delete one listing from "seen" by
        # updating fixture isn't easy. Call mark via sync after removing
        # one property from DB external tracking: create a stale listing.
        from modules.database.property_external_listings_repository import (
            create_property_external_listing,
            list_property_external_listings,
        )
        from modules.database.properties_repository import add_property

        agent = find_agent_by_external_id(
            org,
            "stub_fixture",
            "DH-AGENT-1",
        )
        stale_property = add_property(
            "Stale Addr",
            "CABA",
            org,
            agent_id=agent["id"],
            status="approved",
            property_type="apartment",
        )
        create_property_external_listing(
            org,
            stale_property,
            "remax_web",
            "https://www.remax.com.ar/listings/stale",
            "active",
            external_id="DH-PROP-STALE",
        )

        result = run_integration_sync(integration["id"], org)
        self.assertGreaterEqual(result.listings_deactivated, 1)

        listings = list_property_external_listings(
            stale_property,
            org,
        )
        self.assertEqual(listings[0]["status"], "inactive")

        # Property itself remains
        properties = get_properties(
            org,
            include_all_statuses=True,
        )
        ids = {item["id"] for item in properties}
        self.assertIn(stale_property, ids)


if __name__ == "__main__":
    unittest.main()
