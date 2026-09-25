"""Local QA server for the visit-close sheet. Uses a throwaway database."""

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent))
os.environ["DATABASE_PATH"] = str(ROOT / "qa.db")
os.environ["JRH_AI_PROVIDER"] = "mock"
os.environ.pop("OPENAI_API_KEY", None)
if (ROOT / "qa.db").exists():
    (ROOT / "qa.db").unlink()

from modules.agent_tasks import create_task
from modules.auth import ROLE_AGENT, hash_password
from modules.contacts import create_agent_contact
from modules.database import add_agent, add_organization, add_property, add_user, create_tables
from modules.organization_time import organization_timezone
from web_app import app

create_tables()
org = add_organization("Visit Close QA")
agent = add_agent("QA Agent", "Alto", org)
add_user("visit_qa", hash_password("Password1"), ROLE_AGENT, org, agent_id=agent)
contact = create_agent_contact(
    org,
    agent,
    {"name": "Martín Pérez", "phone": "1155550000", "status": "lead", "source": "manual"},
)
property_id = add_property("Santamarina 1335", "CABA", org, agent_id=agent)
local = datetime.now(organization_timezone(org))
create_task(
    org,
    agent,
    {
        "title": "Visita Santamarina",
        "task_type": "visit",
        "due_date": local.date().isoformat(),
        "due_time": local.strftime("%H:%M"),
        "contact_id": contact["id"],
        "contact_name": contact["name"],
        "property_id": property_id,
    },
)
app.run(host="127.0.0.1", port=8766, debug=False, use_reloader=False)
