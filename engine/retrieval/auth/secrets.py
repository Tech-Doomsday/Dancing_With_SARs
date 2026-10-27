"""Earthdata credentials, centralized. Token preferred; user/password fallback. Never logged."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EarthdataAuth:
    token: str | None = None
    username: str | None = None
    password: str | None = None

    def header(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.username and self.password:
            b = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {b}"}
        return {}

    @property
    def available(self) -> bool:
        return bool(self.token or (self.username and self.password))

    def __repr__(self) -> str:
        """Credentials NEVER appear in repr/str (they end up in logs via f'{client}' chains)."""
        u = self.username if self.username and "SECRET" not in self.username else None
        return (f"EarthdataAuth(token={'set' if self.token else None}, "
                f"username={u!r}, password={'set' if self.username and self.password else None})")

    __str__ = __repr__


def earthdata_auth() -> EarthdataAuth:
    # .env is loaded by settings.load_settings(); real env vars win.
    return EarthdataAuth(
        token=os.environ.get("EARTHDATA_TOKEN") or None,
        username=os.environ.get("EARTHDATA_USERNAME") or None,
        password=os.environ.get("EARTHDATA_PASSWORD") or None,
    )
