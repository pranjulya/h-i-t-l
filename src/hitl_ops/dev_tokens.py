"""Learning-mode demo token minting.

Mints the HS256 identity tokens the demo script assumes, including the scope
claims approvals require. This is a learning-mode utility: it refuses to run in
production, where identities come from the OIDC provider (ADR-008) and the
signing material must never be a shared demo secret.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import jwt

from hitl_ops.config import Settings

DEMO_SCOPES = "ops:read ops:write"


class DemoTokenError(Exception):
    """The demo token cannot be minted in this environment."""


def mint_token(
    *,
    actor: str,
    tenant: str,
    scopes: str,
    minutes: int,
    settings: Settings | None = None,
) -> str:
    resolved = settings or Settings()
    if resolved.is_production:
        raise DemoTokenError("demo token minting is disabled in production")
    if resolved.identity_shared_secret is None:
        raise DemoTokenError("IDENTITY_SHARED_SECRET must be configured to mint demo tokens")
    return jwt.encode(
        {
            "sub": actor,
            "tenant": tenant,
            "iss": resolved.identity_issuer,
            "aud": resolved.identity_audience,
            "scope": scopes,
            "exp": datetime.now(UTC) + timedelta(minutes=minutes),
        },
        resolved.identity_shared_secret,
        algorithm="HS256",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a learning-mode demo identity token")
    parser.add_argument("--actor", required=True, help="subject, e.g. approver-1")
    parser.add_argument("--tenant", default="tenant-1")
    parser.add_argument("--scopes", default=DEMO_SCOPES, help=f"default: {DEMO_SCOPES!r}")
    parser.add_argument("--minutes", type=int, default=30)
    args = parser.parse_args(argv)

    try:
        token = mint_token(
            actor=args.actor,
            tenant=args.tenant,
            scopes=args.scopes,
            minutes=args.minutes,
        )
    except DemoTokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
