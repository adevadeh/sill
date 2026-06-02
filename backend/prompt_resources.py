from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal


# Optional, user-supplied personhood markdown file. If absent, no addendum
# is composed and ``compose_personhood_prompt`` returns an empty string.
PROMPT_RESOURCE_PATH = Path(__file__).resolve().parent / "prompts" / "personhood.md"


@dataclass(frozen=True)
class PromptLibrary:
    raw_markdown: str
    modules: dict[str, str]

    def module(self, key: str) -> str:
        try:
            return self.modules[key]
        except KeyError as exc:
            raise KeyError(
                f"Unknown prompt module: {key!r}. Available: {sorted(self.modules.keys())}"
            ) from exc

    def compose(self, keys: list[str], *, separator: str = "\n\n---\n\n") -> str:
        parts: list[str] = []
        for key in keys:
            text = self.module(key).strip()
            if text:
                parts.append(text)
        return separator.join(parts).strip()


_MODULE_HEADING_RE = re.compile(r"(?m)^## Module\s+(\d+)\s*:\s*(.+?)\s*$")


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def parse_personhood_modules(markdown: str) -> dict[str, str]:
    """
    Parse modules from a personhood markdown file.

    Modules are introduced by headings like ``## Module 1: Core Identity``.
    Returns a dict keyed by:
      - ``module_<n>`` (e.g. ``module_1``)
      - ``<slug>`` (e.g. ``core_identity``)
    """
    matches = list(_MODULE_HEADING_RE.finditer(markdown))
    if not matches:
        return {}

    modules: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        block = markdown[start:end].strip()
        number = m.group(1)
        title = m.group(2)
        key_num = f"module_{number}"
        key_slug = _slugify(title)
        modules[key_num] = block
        modules[key_slug] = block
    return modules


@lru_cache(maxsize=1)
def load_personhood_library() -> PromptLibrary:
    """
    Load the optional personhood markdown library.

    Returns an empty ``PromptLibrary`` if no file is present. Operators who
    want a personhood addendum should provide their own ``prompts/personhood.md``
    relative to this module; the format is documented above in
    ``parse_personhood_modules``.
    """
    if PROMPT_RESOURCE_PATH.exists():
        md = PROMPT_RESOURCE_PATH.read_text(encoding="utf-8")
        return PromptLibrary(raw_markdown=md, modules=parse_personhood_modules(md))
    return PromptLibrary(raw_markdown="", modules={})


PromptKind = Literal["heartbeat", "reflect", "conversation"]


def compose_personhood_prompt(kind: PromptKind) -> str:
    """
    Compose an optional personhood prompt addendum for a given context.

    This shim is intentionally generic: it composes any modules the operator
    has supplied under standard slugs and returns an empty string when no
    personhood markdown is present. Callers (e.g. the worker) prepend their
    own task-specific instructions and include this addendum verbatim.

    Standard slugs consumed per kind (any subset that exists in the library
    is composed; missing slugs are skipped silently):
      - ``heartbeat``    → core_identity, affective_system, reflection_protocols
      - ``reflect``      → core_identity, self_model_maintenance, value_system,
                           narrative_identity, relational_system
      - ``conversation`` → core_identity, relational_system, affective_system,
                           conversational_presence
    """
    lib = load_personhood_library()
    if not lib.modules:
        return ""

    if kind == "heartbeat":
        keys = ["core_identity", "affective_system", "reflection_protocols"]
    elif kind == "reflect":
        keys = [
            "core_identity",
            "self_model_maintenance",
            "value_system",
            "narrative_identity",
            "relational_system",
        ]
    elif kind == "conversation":
        keys = [
            "core_identity",
            "relational_system",
            "affective_system",
            "conversational_presence",
        ]
    else:
        raise ValueError(f"Unknown kind: {kind}")

    existing = [k for k in keys if k in lib.modules]
    return lib.compose(existing)
