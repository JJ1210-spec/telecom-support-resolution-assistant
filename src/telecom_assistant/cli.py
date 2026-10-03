"""Command line: `telecom-assistant <command>` (or `python -m telecom_assistant.cli <command>`).

  check-keys          ping every configured provider and show quota meters
  seed [--demo]       load taxonomy, KB and the synthetic corpus; --demo adds demo users and tickets
  updates             replay versioned update events (ticket resolutions, KB edit + deprecation)
  create-user         create an agent/admin (password prompted)
  eval [--limit N]    run the evaluation suite and write reports/
  drift               compute drift metrics + alerts
  discover            cluster the discovery pool into taxonomy proposals
  reindex SUFFIX      blue/green rebuild into new Qdrant collections, then swap aliases
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import random
import secrets

from .config import Settings
from .services import build_services


def _print(data) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


async def check_keys(settings: Settings) -> None:
    services = build_services(settings)
    rows = {"database": services.db.ping(), "vector_store": await services.index.ping(), "kv": await services.kv.ping()}
    try:
        vec = await services.embedder.embed(["router keeps disconnecting"], task="query")
        rows["embeddings"] = f"ok ({len(vec[0])} dims, {getattr(services.embedder, 'model', '?')})"
    except Exception as exc:  # noqa: BLE001
        rows["embeddings"] = f"error: {exc}"
    if services.reranker:
        rows["rerank"] = "ok" if await services.reranker.rerank("wifi", ["router", "bill"], 2) else "error"
    if services.llm:
        for role in ("triage", "draft", "assist", "judge"):
            try:
                result = await services.llm.json(role, "Return JSON {\"ok\": true}", "ping", None, max_tokens=20)
                rows[f"llm:{role}"] = f"ok via {result.model_id} ({result.latency_ms} ms)"
            except Exception as exc:  # noqa: BLE001
                rows[f"llm:{role}"] = f"error: {exc}"
        rows["quota"] = await services.llm.quota_report()
    _print(rows)


async def seed(settings: Settings, demo: bool, demo_tickets: int) -> None:
    from .api.security import Accounts
    from .seed import bootstrap, read_jsonl
    from .tickets.desk import SupportDesk

    services = build_services(settings)
    _print(await bootstrap(services))
    if not demo:
        return
    accounts = Accounts(services.db)
    password = os.getenv("DEMO_PASSWORD") or secrets.token_urlsafe(9)
    created = []
    for email, role, name, region in [("admin@resolvedesk.dev", "admin", "Asha Admin", None),
                                      ("agent@resolvedesk.dev", "agent", "Arjun Agent", None),
                                      ("customer@resolvedesk.dev", "customer", "Priya Customer", "Koramangala")]:
        try:
            accounts.create(email, password, role, name, region)
            created.append(email)
        except ValueError:
            pass
    if created:
        print(f"Demo accounts {created} use password: {password}")
    if demo_tickets <= 0:
        return
    desk = SupportDesk(services)
    rng = random.Random(7)
    rows = rng.sample(read_jsonl("unresolved_tickets.jsonl"), demo_tickets)
    for index, row in enumerate(rows):
        email = f"demo{index}@resolvedesk.dev"
        try:
            user = accounts.create(email, password, "customer", f"Demo Customer {index}", "Koramangala")
        except ValueError:
            continue
        user["role"] = "customer"
        ticket = await desk.create_ticket(user, row["body"], row.get("product_hint"), "Koramangala", None)
        print("created", ticket["id"], "-", row["subject"])
    while desk.tasks:
        await asyncio.gather(*list(desk.tasks), return_exceptions=True)
    await services.outbox.run_once()


async def updates(settings: Settings) -> None:
    from .seed import apply_updates

    _print(await apply_updates(build_services(settings)))


async def evaluate(settings: Settings, limit: int | None, concurrency: int, no_ablation: bool) -> None:
    from .evaluation import run_eval, to_markdown
    from .tickets.desk import SupportDesk

    services = build_services(settings)
    services.registry.seed()
    await services.indexer.ensure()
    if services.index.backend == "local":
        await services.indexer.rebuild_from_db()
    report = await run_eval(SupportDesk(services), limit=limit, concurrency=concurrency, ablation=not no_ablation)
    print(to_markdown(report))
    await services.langfuse.flush()


async def drift(settings: Settings) -> None:
    _print(build_services(settings, llm=False).drift.compute())


async def discover(settings: Settings) -> None:
    services = build_services(settings)
    if services.index.backend == "local":
        await services.indexer.rebuild_from_db()
    _print(await services.discovery.run())


async def reindex(settings: Settings, suffix: str) -> None:
    from .gateways.vectors import QdrantIndex

    services = build_services(settings)
    if not isinstance(services.index, QdrantIndex):
        raise SystemExit("reindex requires the Qdrant backend")
    index = services.index
    staging = {"tickets": f"tickets_{suffix}", "kb": f"kb_{suffix}"}
    index.override = dict(staging)  # write into the new physical collections, not the live aliases
    await services.indexer.ensure()
    result = await services.indexer.rebuild_from_db()  # embeddings come from the hash cache: no token spend
    index.override = {}
    for alias, physical in staging.items():
        await index.swap_alias(alias, physical)  # atomic cut-over; the old collection stays for rollback
    _print({"rebuilt": result, "aliases": staging})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-keys")
    p = sub.add_parser("seed")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--demo-tickets", type=int, default=0)
    sub.add_parser("updates")
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=["agent", "admin", "customer"], default="agent")
    p.add_argument("--name", default="")
    p = sub.add_parser("eval")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--no-ablation", action="store_true")
    sub.add_parser("drift")
    sub.add_parser("discover")
    p = sub.add_parser("reindex")
    p.add_argument("suffix")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "check-keys":
        asyncio.run(check_keys(settings))
    elif args.command == "seed":
        asyncio.run(seed(settings, args.demo, args.demo_tickets))
    elif args.command == "updates":
        asyncio.run(updates(settings))
    elif args.command == "create-user":
        from .api.security import Accounts
        from .db import Database

        password = getpass.getpass("Password (10+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match")
        db = Database(settings.sqlalchemy_url)
        db.create_all()
        _print(Accounts(db).create(args.email, password, args.role, args.name))
    elif args.command == "eval":
        asyncio.run(evaluate(settings, args.limit, args.concurrency, args.no_ablation))
    elif args.command == "drift":
        asyncio.run(drift(settings))
    elif args.command == "discover":
        asyncio.run(discover(settings))
    elif args.command == "reindex":
        asyncio.run(reindex(settings, args.suffix))


if __name__ == "__main__":
    main()
