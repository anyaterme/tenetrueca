from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IdentityProfile:
    provider: str
    external_id: str
    email: str
    first_name: str = ''
    last_name: str = ''


class IdentityProvider(Protocol):
    code: str

    def authenticate(self, credentials: dict) -> IdentityProfile | None:
        ...


class LocalIdentityProvider:
    code = 'local'

    def authenticate(self, credentials: dict) -> IdentityProfile | None:
        return None
