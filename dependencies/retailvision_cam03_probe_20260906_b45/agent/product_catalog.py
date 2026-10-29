"""Canonical product-key boundary for Product Analytics MCP calls."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


def _token(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True)
class ProductCatalog:
    """Resolve public product aliases to deployment-owned canonical keys."""

    _canonical_keys: frozenset[str]
    _aliases: Mapping[str, str]

    @classmethod
    def empty(cls) -> "ProductCatalog":
        return cls(frozenset(), {})

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, Iterable[str]]
    ) -> "ProductCatalog":
        canonical_keys: set[str] = set()
        aliases: dict[str, str] = {}
        for raw_canonical, raw_aliases in mapping.items():
            canonical = raw_canonical.strip()
            if not canonical:
                raise ValueError("product_catalog_invalid")
            canonical_keys.add(canonical)
            for alias in (canonical, *tuple(raw_aliases)):
                if not isinstance(alias, str) or not alias.strip():
                    raise ValueError("product_catalog_invalid")
                normalized = _token(alias)
                existing = aliases.get(normalized)
                if existing is not None and existing != canonical:
                    raise ValueError("product_catalog_invalid")
                aliases[normalized] = canonical
        return cls(frozenset(canonical_keys), aliases)

    @classmethod
    def from_alias_spec(cls, spec: str | None) -> "ProductCatalog":
        if not spec or not spec.strip():
            return cls.empty()
        mapping: dict[str, set[str]] = {}
        for entry in spec.split(","):
            left, separator, right = entry.partition("=")
            canonical = right.strip()
            public_aliases = {alias.strip() for alias in left.split("/") if alias.strip()}
            if not separator or not canonical or not public_aliases:
                raise ValueError("product_catalog_invalid")
            mapping.setdefault(canonical, set()).update(public_aliases)
        return cls.from_mapping(mapping)

    def resolve(self, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("product_key_unknown")
        canonical = self._aliases.get(_token(value))
        if canonical is None or canonical not in self._canonical_keys:
            raise ValueError("product_key_unknown")
        return canonical
