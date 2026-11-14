"""Owner-managed research team templates; saving never schedules or runs agents."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from harness.agents.workflows import research_workflow


def validate_template(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"name", "specialists"}:
        raise ValueError("Template must contain only name and specialists")
    name = value["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("Template name must have 1-80 characters")
    specialists = value["specialists"]
    if not isinstance(specialists, list) or not 2 <= len(specialists) <= 4:
        raise ValueError("Choose 2-4 specialists")
    if any(not isinstance(agent, dict) or set(agent) != {"role", "prompt"}
           for agent in specialists):
        raise ValueError("Each specialist must contain only role and prompt")
    # Reuse the execution validator, including role and assignment bounds. The
    # verifier/synthesizer dependency graph remains fixed and cannot be saved.
    graph = research_workflow("Validate research template", specialists)
    return {"name": name.strip(), "specialists": [
        {"role": node["role"], "prompt": node["prompt"]}
        for node in graph["nodes"][:len(specialists)]
    ]}


class WorkflowTemplates:
    def __init__(self, store):
        self.store = store
        self.store.db.execute("""CREATE TABLE IF NOT EXISTS workflow_templates(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
            specialists TEXT NOT NULL, updated_at INTEGER NOT NULL)""")

    @staticmethod
    def _template(row):
        return {"id": row["id"], "name": row["name"],
                "specialists": json.loads(row["specialists"]), "updatedAt": row["updated_at"]}

    def list(self) -> list[dict[str, Any]]:
        return [self._template(row) for row in self.store.db.execute(
            "SELECT * FROM workflow_templates ORDER BY updated_at DESC, rowid DESC")]

    def select(self, template_id: str) -> dict[str, Any]:
        if not isinstance(template_id, str) or not template_id:
            raise ValueError("Template not found")
        row = self.store.db.execute("SELECT * FROM workflow_templates WHERE id=?", (template_id,)).fetchone()
        if row is None:
            raise ValueError("Template not found")
        return self._template(row)

    def save(self, value: Any) -> dict[str, Any]:
        template = validate_template(value)

        def work():
            if self.store.db.execute("SELECT 1 FROM workflow_templates WHERE name_key=?",
                                     (template["name"].casefold(),)).fetchone():
                raise ValueError("A template with that name already exists; choose another name")
            if self.store.db.execute("SELECT COUNT(*) FROM workflow_templates").fetchone()[0] >= 20:
                raise ValueError("Keep at most 20 workflow templates")
            template_id = str(uuid.uuid4())
            self.store.db.execute("INSERT INTO workflow_templates VALUES (?,?,?,?,?)", (
                template_id, template["name"], template["name"].casefold(),
                json.dumps(template["specialists"],ensure_ascii=False), int(time.time() * 1000)))
            return self.select(template_id)

        return self.store._tx(work)

    def delete(self, template_id: str) -> bool:
        self.select(template_id)
        return bool(self.store.db.execute("DELETE FROM workflow_templates WHERE id=?", (template_id,)).rowcount)

    def forget_claims(self, phrases):
        """Remove saved instructions containing explicitly forgotten claims."""
        removed=0
        for template in self.list():
            values=[template['name']]+[agent[key] for agent in template['specialists'] for key in ('role','prompt')]
            if any(phrase and phrase in value for phrase in phrases for value in values):
                removed+=int(self.delete(template['id']))
        return removed
