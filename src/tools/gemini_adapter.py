"""Translate OpenAI-Realtime function specs to Gemini Live tool objects.

The kids-teacher tools framework keeps its specs in OpenAI-Realtime
shape (a flat dict with ``type``, ``name``, ``description``,
``parameters``). The Gemini Live SDK takes ``types.Tool`` objects
wrapping ``types.FunctionDeclaration``. This adapter is the only
place that boundary lives — fixing a Gemini SDK schema rename means
editing one file.

Stub specs (allowlist artifacts from ``build_session_payload`` with
no ``parameters`` field) are quietly skipped — the existing
memory/face builders cover those names with their own
``FunctionDeclaration`` objects.

Malformed specs are logged as warnings and skipped so a single bad
entry can't take down the session.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def build_gemini_tools(types_module: Any, specs: Any) -> list:
    """Translate a list of OpenAI-shaped specs to ``types.Tool`` objects.

    Each output ``types.Tool`` wraps a single ``FunctionDeclaration`` —
    matching the existing kids-teacher pattern (``_build_memory_tool``
    et al. also wrap one declaration per Tool). Returns an empty list
    for ``None`` or ``[]`` input.
    """
    out: list = []
    if not specs:
        return out
    for spec in specs:
        try:
            decl = _spec_to_function_declaration(types_module, spec)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[tools.gemini_adapter] dropping malformed spec %r: %s", spec, exc
            )
            continue
        if decl is None:
            continue
        out.append(types_module.Tool(function_declarations=[decl]))
    return out


def _spec_to_function_declaration(
    types_module: Any, spec: Any
) -> Optional[Any]:
    if not isinstance(spec, Mapping):
        raise TypeError("spec must be a mapping")
    spec_type = spec.get("type")
    if spec_type not in (None, "function"):
        # Future: tools other than function-calls (e.g. retrieval) live
        # under different SDK shapes; ignore them rather than crashing.
        return None
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("spec missing 'name'")

    parameters = spec.get("parameters")
    if not isinstance(parameters, Mapping) or not parameters:
        # Stub spec from the profile.allowed_tools loop in
        # build_session_payload — no schema, nothing to register.
        # Quietly skip; explicit memory/face builders own those names.
        return None

    kwargs: dict[str, Any] = {
        "name": name,
        "parameters_json_schema": dict(parameters),
    }
    description = spec.get("description")
    if description:
        kwargs["description"] = str(description)
    return types_module.FunctionDeclaration(**kwargs)


__all__ = ["build_gemini_tools"]
