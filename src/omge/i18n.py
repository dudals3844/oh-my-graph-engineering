"""Message catalogs. English is the source of truth; another locale falls back to it per key."""

from __future__ import annotations

import os

from .locales import checks, cli, graph, hooks, library, server, store

_MODULES = (checks, cli, graph, hooks, library, server, store)
EN = {k: v for m in _MODULES for k, v in m.EN.items()}
CATALOGS = {"en": EN, "ko": {k: v for m in _MODULES for k, v in m.KO.items()}}


def lang() -> str:
    """`OMGE_LANG`, else the POSIX message locale (`ko_KR.UTF-8` → ko), else en."""
    for var in ("OMGE_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        if value := os.environ.get(var):
            code = value.split("_")[0].split(".")[0].lower()
            return code if code in CATALOGS else "en"
    return "en"


def t(key: str, **kw: object) -> str:
    """The message `key` in the current locale, formatted with `kw` (str.format)."""
    return CATALOGS[lang()].get(key, EN[key]).format(**kw)


def variants(key: str) -> list[str]:
    """`key` in every locale, for parsers that must accept any of them."""
    return list(dict.fromkeys(c.get(key, EN[key]) for c in CATALOGS.values()))
