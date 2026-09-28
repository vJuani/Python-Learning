"""Administrative CLIs must name an organization. They never assume id 1."""

from __future__ import annotations

import os
import unittest

from create_admin import parse_args
from modules.cli_tenant import CliOrganizationError, get_cli_organization_id


class CliOrganizationTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("ORGANIZATION_ID")
        os.environ.pop("ORGANIZATION_ID", None)
        os.environ.pop("DEFAULT_ORGANIZATION_ID", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ORGANIZATION_ID", None)
        else:
            os.environ["ORGANIZATION_ID"] = self._saved

    def test_missing_organization_fails(self):
        with self.assertRaises(CliOrganizationError):
            get_cli_organization_id()

    def test_explicit_organization_is_used(self):
        os.environ["ORGANIZATION_ID"] = "7"
        self.assertEqual(get_cli_organization_id(), 7)

    def test_invalid_organization_fails(self):
        os.environ["ORGANIZATION_ID"] = "0"
        with self.assertRaises(CliOrganizationError):
            get_cli_organization_id()

    def test_create_admin_requires_organization_id(self):
        with self.assertRaises(SystemExit):
            parse_args([])

    def test_create_admin_accepts_an_explicit_id(self):
        args = parse_args(["--organization-id", "4"])
        self.assertEqual(args.organization_id, 4)
