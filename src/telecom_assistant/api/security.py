"""Accounts, sessions, CSRF and role checks.

Passwords: salted scrypt. Sessions: random token in an HttpOnly SameSite=Lax cookie, stored hashed.
Mutations require the per-session CSRF token in `X-CSRF-Token`. Five failed logins lock an account for
ten minutes; login/register/ticket creation are also rate limited per IP via the shared KV store.
Roles: customer and admin. Public registration can only create customers.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException, Request

from ..db import Database, sessions, users, utc_now

SESSION_SECONDS = 12 * 60 * 60
COOKIE = "rd_session"
EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}$")
ROLES = {"customer", "admin"}


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, expected = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


class Accounts:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, email: str, password: str, role: str = "customer", name: str = "",
               region: str | None = None) -> dict:
        email = email.strip().casefold()
        if role not in ROLES:
            raise ValueError("Invalid role")
        if not 10 <= len(password) <= 128:
            raise ValueError("Use a password of 10-128 characters")
        if len(email) > 254 or not EMAIL.match(email):
            raise ValueError("Enter a valid email address")
        user = {"id": f"usr_{uuid.uuid4().hex[:16]}", "email": email, "role": role, "name": name.strip()[:120],
                "region": (region or "").strip()[:80] or None}
        with self.db.tx() as con:
            if con.execute(sa.select(users.c.id).where(users.c.email == email)).first():
                raise ValueError("An account with this email already exists")
            con.execute(users.insert().values(**user, password_hash=password_hash(password), created_at=utc_now()))
        return user

    def authenticate(self, email: str, password: str) -> dict | None:
        with self.db.tx() as con:
            row = con.execute(sa.select(users).where(users.c.email == email.strip().casefold())).first()
            if not row or row.role not in ROLES or row.locked_until > int(time.time()):
                return None
            if not password_matches(password, row.password_hash):
                failures = row.failed_attempts + 1
                con.execute(users.update().where(users.c.id == row.id).values(
                    failed_attempts=failures, locked_until=int(time.time()) + 600 if failures >= 5 else 0))
                return None
            con.execute(users.update().where(users.c.id == row.id).values(failed_attempts=0, locked_until=0))
            return {"id": row.id, "email": row.email, "role": row.role, "name": row.name, "region": row.region}

    def new_session(self, user_id: str) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(24)
        with self.db.tx() as con:
            con.execute(sessions.delete().where(sessions.c.expires_at < int(time.time())))
            con.execute(sessions.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                                 user_id=user_id, csrf_token=csrf,
                                                 expires_at=int(time.time()) + SESSION_SECONDS))
        return token, csrf

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.db.read() as con:
            row = con.execute(sa.select(users.c.id, users.c.email, users.c.role, users.c.name, users.c.region,
                                        sessions.c.csrf_token).join(sessions, sessions.c.user_id == users.c.id)
                              .where(sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                                     sessions.c.expires_at > int(time.time()))).first()
        return dict(row._mapping) if row and row.role in ROLES else None

    def revoke(self, token: str) -> None:
        with self.db.tx() as con:
            con.execute(sessions.delete().where(sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()))

    def list(self, role: str | None = None) -> list[dict]:
        query = sa.select(users.c.id, users.c.email, users.c.name, users.c.role, users.c.region, users.c.created_at)
        query = query.where(users.c.role.in_(ROLES))
        if role:
            query = query.where(users.c.role == role)
        with self.db.read() as con:
            return [{**dict(r._mapping), "created_at": r.created_at.isoformat()} for r in con.execute(query)]


def current_user(request: Request) -> dict:
    user = request.app.state.accounts.session(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def require(role: str, write: bool = False):
    def dependency(request: Request, user: dict = Depends(current_user),
                   x_csrf_token: str | None = Header(default=None)) -> dict:
        if user["role"] != role:
            raise HTTPException(403, f"{role.title()} role required")
        if write and (not x_csrf_token or not hmac.compare_digest(user["csrf_token"], x_csrf_token)):
            raise HTTPException(403, "CSRF token missing or invalid")
        return user

    return dependency


customer_read = require("customer")
customer_write = require("customer", write=True)
admin_read = require("admin")
admin_write = require("admin", write=True)
