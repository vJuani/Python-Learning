"""Marketing IA chat workspace shell and conversation actions UI."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TEST_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = str(Path(_TEST_TMP.name) / "test_mkt_chat_ux.db")
os.environ["PRIVATE_UPLOAD_ROOT"] = str(Path(_TEST_TMP.name) / "uploads")
os.environ.pop("DATABASE_URL", None)

from modules.auth import ROLE_AGENT, hash_password
from modules.config import apply_config
from modules.database import add_agent, add_organization, add_user, create_tables
from modules.database.marketing_conversations_repository import (
    create_conversation_folder,
    create_marketing_conversation,
    update_marketing_conversation,
)
from modules.marketing_chat_service import delete_conversation, pin_conversation
from web_app import app


class MarketingChatUxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apply_config(app)
        app.config.update(TESTING=True, SECRET_KEY="mkt-chat-ux")
        create_tables()
        cls.org = add_organization("Chat UX Org")
        cls.agent_id = add_agent("Jose Chat", "Alto", cls.org)
        cls.user_id = add_user(
            "chat_ux_agent",
            hash_password("Password1"),
            ROLE_AGENT,
            cls.org,
            agent_id=cls.agent_id,
            first_name="Jose",
            last_name="Barreiro",
        )

    def _user(self):
        return {
            "id": self.user_id,
            "role": ROLE_AGENT,
            "organization_id": self.org,
            "agent_id": self.agent_id,
        }

    def _client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.user_id
            session["role"] = ROLE_AGENT
            session["organization_id"] = self.org
        return client

    def test_chat_shell_and_workspace_groups_render(self):
        folder = create_conversation_folder(self.org, user_id=self.user_id, name="Publicaciones")
        pinned = create_marketing_conversation(
            self.org,
            user_id=self.user_id,
            title="Italia 1341 larga para ellipsis de sidebar",
        )
        pin_conversation(self.org, self._user(), pinned["id"])
        moved = create_marketing_conversation(
            self.org,
            user_id=self.user_id,
            title="Chat en carpeta",
        )
        update_marketing_conversation(moved["id"], self.org, folder_id=folder["id"], touch=False)
        page = self._client().get("/marketing")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("is-marketing-chat-page", html)
        self.assertIn("data-mkt-resize", html)
        self.assertIn("data-mkt-conv-menu", html)
        self.assertIn("Fijadas", html)
        self.assertIn("Carpetas", html)
        self.assertIn("Publicaciones", html)
        self.assertIn("Italia 1341", html)
        self.assertIn("data-mkt-chat-search", html)
        css = (
            Path(__file__)
            .resolve()
            .parent.parent.joinpath("static", "css", "marketing-chat.css")
            .read_text(encoding="utf-8")
        )
        self.assertIn("overflow: hidden", css)
        self.assertIn("--mkt-sidebar-width", css)
        self.assertIn(".site-footer", css)
        js = (
            Path(__file__)
            .resolve()
            .parent.parent.joinpath("static", "js", "marketing-chat.js")
            .read_text(encoding="utf-8")
        )
        self.assertIn("contextmenu", js)
        self.assertIn("mkt-chat-sidebar-width", js)

    def test_delete_conversation_keeps_assets_contract(self):
        conversation = create_marketing_conversation(
            self.org,
            user_id=self.user_id,
            title="Borrar chat",
        )
        delete_conversation(self.org, self._user(), conversation["id"])
        page = self._client().get(f"/marketing/c/{conversation['id']}")
        self.assertIn(page.status_code, {302, 404})


if __name__ == "__main__":
    unittest.main()
