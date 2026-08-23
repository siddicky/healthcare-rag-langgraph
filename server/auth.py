from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeAlias, assert_never

from langgraph_sdk import Auth
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

Resource: TypeAlias = Literal["runs", "threads", "crons", "assistants", "store"]
Action: TypeAlias = Literal[
    "create",
    "read",
    "update",
    "delete",
    "search",
    "create_run",
    "put",
    "get",
    "list_namespaces",
]
PrincipalValue: TypeAlias = str | bool | Sequence[str]
ScopeFilter: TypeAlias = dict[str, Any]

PUBLIC_PATHS: Final = frozenset({"/ok", "/info"})
STUDIO_IDENTITY: Final = "langgraph-studio-user"


@dataclass(frozen=True, slots=True)
class AuthConfigurationError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


class ScopeUser(Mapping[str, PrincipalValue]):
    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, PrincipalValue]) -> None:
        identity = data.get("identity")
        if not isinstance(identity, str) or not identity:
            raise AuthConfigurationError("Authenticated principal has no identity")
        self._data = dict(data)

    @property
    def identity(self) -> str:
        value = self._data["identity"]
        if not isinstance(value, str):
            raise AuthConfigurationError("Authenticated principal identity is not text")
        return value

    @property
    def is_authenticated(self) -> bool:
        value = self._data.get("is_authenticated", True)
        return value if isinstance(value, bool) else True

    @property
    def display_name(self) -> str:
        value = self._data.get("display_name", self.identity)
        return value if isinstance(value, str) else self.identity

    @property
    def permissions(self) -> Sequence[str]:
        value = self._data.get("permissions", ())
        if isinstance(value, Sequence) and not isinstance(value, str):
            return value
        return ()

    def __getitem__(self, key: str) -> PrincipalValue:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class AuthPolicyEngine:
    __slots__ = ("auth",)

    def __init__(self, auth: Auth) -> None:
        if auth._authenticate_handler is None:
            raise AuthConfigurationError("Auth instance has no authenticate handler")
        self.auth = auth

    async def run_policy(
        self,
        resource: Resource,
        action: Action,
        user: ScopeUser,
        value: MutableMapping[str, Any],
    ) -> ScopeFilter | None:
        ctx = PolicyContext(
            permissions=user.permissions,
            user=user,
            resource=resource,
            action=action,
        )
        handler = self._handler(resource, action)
        if handler is None:
            return None
        result = await handler(ctx=ctx, value=value)
        match result:
            case None | True:
                return None
            case False:
                raise Auth.exceptions.HTTPException(status_code=403, detail="Forbidden")
            case dict() as scope_filter:
                return scope_filter
            case unreachable:
                assert_never(unreachable)

    def _handler(self, resource: Resource, action: Action):
        requested = (resource, action)
        cached = self.auth._handler_cache.get(requested)
        if cached is not None:
            return cached
        for key in (
            requested,
            (resource, "*"),
            ("*", action),
            ("*", "*"),
        ):
            handlers = self.auth._handlers.get(key)
            if handlers:
                handler = handlers[-1]
                self.auth._handler_cache[requested] = handler
                return handler
        return self.auth._global_handlers[-1] if self.auth._global_handlers else None


@dataclass(frozen=True, slots=True)
class PolicyContext:
    permissions: Sequence[str]
    user: ScopeUser
    resource: Resource
    action: Action


def merge_scope_filter(
    requested: Mapping[str, Any] | None, scope_filter: Mapping[str, Any]
) -> ScopeFilter:
    return {**(requested or {}), **scope_filter}


def require_scope_match(
    metadata: Mapping[str, Any], scope_filter: Mapping[str, Any]
) -> None:
    for key, expected in scope_filter.items():
        actual = metadata.get(key)
        if not _filter_term_matches(actual, expected):
            raise Auth.exceptions.HTTPException(status_code=404, detail="Not Found")


def _filter_term_matches(actual: Any, expected: Any) -> bool:
    if not isinstance(expected, Mapping):
        return actual == expected
    if "$eq" in expected:
        return actual == expected["$eq"]
    if "$contains" in expected:
        contained = expected["$contains"]
        if not isinstance(actual, Sequence) or isinstance(actual, str):
            return False
        if isinstance(contained, Sequence) and not isinstance(contained, str):
            return all(item in actual for item in contained)
        return contained in actual
    return False


class AuthMiddleware:
    __slots__ = ("app", "engine", "local_dev")

    def __init__(self, app: ASGIApp, auth: Auth, local_dev: bool = False) -> None:
        self.app = app
        self.engine = AuthPolicyEngine(auth)
        self.local_dev = local_dev

    @classmethod
    def as_starlette(cls, auth: Auth, local_dev: bool = False) -> Middleware:
        return Middleware(cls, auth=auth, local_dev=local_dev)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        user = await self._authenticate(scope)
        if user is None:
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(
                scope, receive, send
            )
            return
        scope["user"] = user
        scope["auth"] = AuthCredentials(list(user.permissions))
        await self.app(scope, receive, send)

    async def _authenticate(self, scope: Scope) -> ScopeUser | None:
        handler = self.engine.auth._authenticate_handler
        if handler is None:
            return None
        headers = dict(scope.get("headers", ()))
        authorization = Headers(scope=scope).get("authorization")
        try:
            principal = await handler(
                method=str(scope.get("method", "")),
                path=str(scope.get("path", "")),
                headers=headers,
                authorization=authorization,
            )
        except Auth.exceptions.HTTPException as exc:
            if self.local_dev and exc.status_code == 401:
                return _studio_user()
            return None
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            UnicodeError,
        ):
            return None
        return _scope_user(principal)


def _studio_user() -> ScopeUser:
    return ScopeUser({"identity": STUDIO_IDENTITY, "kind": "StudioUser"})


def _scope_user(principal: Any) -> ScopeUser | None:
    if isinstance(principal, str):
        return ScopeUser({"identity": principal})
    if isinstance(principal, Mapping):
        identity = principal.get("identity")
        if not isinstance(identity, str) or not identity:
            return None
        data: dict[str, PrincipalValue] = {"identity": identity}
        for key in (
            "display_name",
            "is_authenticated",
            "permissions",
            "kind",
            "role",
            "sub_role",
            "internal_owner",
        ):
            value = principal.get(key)
            if isinstance(value, str | bool):
                data[key] = value
            elif isinstance(value, Sequence) and not isinstance(value, str):
                strings = tuple(item for item in value if isinstance(item, str))
                if len(strings) == len(value):
                    data[key] = strings
        return ScopeUser(data)
    identity = getattr(principal, "identity", None)
    if not isinstance(identity, str) or not identity:
        return None
    return ScopeUser(
        {
            "identity": identity,
            "display_name": getattr(principal, "display_name", identity),
            "is_authenticated": getattr(principal, "is_authenticated", True),
            "permissions": getattr(principal, "permissions", ()),
        }
    )


def load_auth_instance(path: str | None) -> Auth:
    if path is None or ":" not in path:
        raise AuthConfigurationError("Auth path must be configured as module.py:name")
    module_path, attribute = path.rsplit(":", 1)
    module_name = module_path.removeprefix("./").removesuffix(".py").replace("/", ".")
    loaded = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(loaded, Auth):
        raise AuthConfigurationError("Configured auth object is not an Auth instance")
    return loaded
