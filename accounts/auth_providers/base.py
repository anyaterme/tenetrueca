from abc import ABC, abstractmethod

from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest


class AuthenticationProvider(ABC):
    @abstractmethod
    def authenticate(
        self,
        username: str,
        password: str,
        request: HttpRequest | None = None,
    ) -> AbstractBaseUser | None:
        raise NotImplementedError
