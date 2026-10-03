"""Versioned taxonomy registry (data, not code).

The triage prompt, the intake chips and the clarifying-question engine all read the *live* registry,
so a class approved by an admin (manually or from drift discovery) is usable on the next request
with no retraining and no deploy. Every change bumps the registry version and is audit-logged.
"""

from __future__ import annotations

import json
from importlib import resources

import sqlalchemy as sa

from ..db import Database, audit_log, taxonomy, taxonomy_versions, utc_now


def seed_spec() -> dict:
    return json.loads(resources.files("telecom_assistant.resources").joinpath("taxonomy_seed.json")
                      .read_text(encoding="utf-8"))


AREAS = {area["id"]: area for area in seed_spec()["areas"]}


class TaxonomyRegistry:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: tuple[int, list[dict]] | None = None

    def version(self) -> int:
        with self.db.read() as con:
            return int(con.execute(sa.select(sa.func.max(taxonomy_versions.c.version))).scalar() or 0)

    def classes(self, include_deprecated: bool = False) -> list[dict]:
        version = self.version()
        if self._cache and self._cache[0] == version and not include_deprecated:
            return self._cache[1]
        query = sa.select(taxonomy).order_by(taxonomy.c.area, taxonomy.c.intent)
        if not include_deprecated:
            query = query.where(taxonomy.c.status == "active")
        with self.db.read() as con:
            rows = [dict(r._mapping) for r in con.execute(query)]
        if not include_deprecated:
            self._cache = (version, rows)
        return rows

    def by_intent(self) -> dict[str, dict]:
        return {c["intent"]: c for c in self.classes()}

    def areas(self) -> list[dict]:
        counts: dict[str, int] = {}
        for c in self.classes():
            counts[c["area"]] = counts.get(c["area"], 0) + 1
        return [{**area, "issues": counts.get(area["id"], 0)} for area in AREAS.values()] + (
            [{"id": a, "label": a.title(), "icon": "dot", "issues": n} for a, n in counts.items() if a not in AREAS])

    def seed(self) -> int:
        spec = seed_spec()
        with self.db.tx() as con:
            existing = {r[0] for r in con.execute(sa.select(taxonomy.c.intent))}
            fresh = [c for c in spec["classes"] if c["intent"] not in existing]
            if not fresh:
                return 0
            version = int(con.execute(sa.select(sa.func.max(taxonomy_versions.c.version))).scalar() or 0) + 1
            con.execute(taxonomy.insert(), [{**c, "status": "active", "version_added": version} for c in fresh])
            con.execute(taxonomy_versions.insert().values(
                version=version, created_by="seed", created_at=utc_now(),
                changelog={"added": [c["intent"] for c in fresh]}))
        self._cache = None
        return len(fresh)

    def apply_change(self, actor: str, action: str, data: dict) -> int:
        """add | update | deprecate | merge. Returns the new taxonomy version."""
        with self.db.tx() as con:
            version = int(con.execute(sa.select(sa.func.max(taxonomy_versions.c.version))).scalar() or 0) + 1
            intent = data["intent"]
            if action == "add":
                fields = {k: data[k] for k in ("label", "category", "product", "area")}
                fields["description"] = data.get("description", "")
                fields["sensitive"] = bool(data.get("sensitive", False))
                exists = con.execute(sa.select(taxonomy.c.intent).where(taxonomy.c.intent == intent)).first()
                if exists:
                    con.execute(taxonomy.update().where(taxonomy.c.intent == intent)
                                .values(**fields, status="active"))
                else:
                    con.execute(taxonomy.insert().values(intent=intent, status="active", version_added=version,
                                                         **fields))
            elif action == "update":
                fields = {k: data[k] for k in ("label", "category", "product", "area", "description", "sensitive")
                          if k in data}
                con.execute(taxonomy.update().where(taxonomy.c.intent == intent).values(**fields))
            elif action == "deprecate":
                con.execute(taxonomy.update().where(taxonomy.c.intent == intent).values(status="deprecated"))
            elif action == "merge":
                con.execute(taxonomy.update().where(taxonomy.c.intent == intent).values(
                    status="deprecated", description=f"Merged into {data['into']}"))
            else:
                raise ValueError(f"Unknown taxonomy action {action}")
            con.execute(taxonomy_versions.insert().values(version=version, created_by=actor, created_at=utc_now(),
                                                          changelog={"action": action, **data}))
            con.execute(audit_log.insert().values(actor_id=actor, action=f"taxonomy.{action}", entity="taxonomy",
                                                  entity_id=intent, diff=data, created_at=utc_now()))
        self._cache = None
        return version

    def history(self, limit: int = 20) -> list[dict]:
        with self.db.read() as con:
            rows = con.execute(sa.select(taxonomy_versions).order_by(taxonomy_versions.c.version.desc())
                               .limit(limit)).all()
        return [{"version": r.version, "created_by": r.created_by, "changelog": r.changelog,
                 "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
