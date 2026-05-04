import re
from typing import Any, Iterable, Optional

# ----------------------------
# Helpers: picking files
# ----------------------------



_SBML_RE = re.compile(r"\.(xml|sbml)$", re.IGNORECASE)
_SEDML_RE = re.compile(r"\.sedml$", re.IGNORECASE)


def _iter_entry_files(entry: Any) -> Iterable[Any]:
    if entry is None:
        return []
    if isinstance(entry, (list, tuple)):
        return entry
    if isinstance(entry, dict):
        for key in ("files", "main_files", "model_files"):
            v = entry.get(key)
            if isinstance(v, (list, tuple)):
                return v
        return []
    try:
        return list(entry)
    except TypeError:
        return []


def _file_name(obj: Any) -> str:
    return getattr(obj, "name", str(obj))


def find_first_sedml(entry_files: Iterable[Any]) -> Optional[Any]:
    for f in entry_files:
        if _SEDML_RE.search(_file_name(f)):
            return f
    return None


def find_first_sbml(entry_files: Iterable[Any]) -> Optional[Any]:
    candidates = []
    for f in entry_files:
        name = _file_name(f)
        if _SEDML_RE.search(name):
            continue
        if _SBML_RE.search(name):
            candidates.append(f)

    # Prefer SBML-ish names
    for key in ("sbml", "model"):
        for c in candidates:
            if key in _file_name(c).lower():
                return c

    return candidates[0] if candidates else None

