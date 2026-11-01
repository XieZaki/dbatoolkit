"""Oracle session-principal identity helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OraclePrincipal:
    """Oracle login principal, separate from the credential's login string."""

    raw_name: str | None
    source: str
    verified: bool

    @classmethod
    def from_runtime(cls, raw_name: str) -> OraclePrincipal:
        if not raw_name or any(ord(char) < 32 or ord(char) == 127 for char in raw_name):
            raise ValueError("Oracle runtime principal is empty or contains control characters")
        return cls(raw_name, "RUNTIME_SESSION_USER", True)

    @classmethod
    def unverified(cls, credential_username: str | None) -> OraclePrincipal:
        return cls(credential_username, "CREDENTIAL_USERNAME", False)

    @property
    def quoted_required(self) -> bool:
        return not bool(self.raw_name and re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", self.raw_name))

    @property
    def verification(self) -> str:
        return "VERIFIED" if self.verified else "UNVERIFIED"

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "credential_username": self.raw_name if not self.verified else None,
            "database_principal": self.raw_name if self.verified else "UNKNOWN",
            "source": self.source,
            "verified": self.verified,
            "verification": self.verification,
            "quoted_required": self.quoted_required if self.verified else False,
        }
