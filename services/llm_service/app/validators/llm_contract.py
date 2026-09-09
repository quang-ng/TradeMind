"""dqflow data-quality contract for one raw `/analyze` model response.

`structural.py` parses the JSON; this module is the declarative value gate
that runs before `LLMOutput`: `action` is one of the three allowed verbs,
`confidence` is a number in [0, 1], and the free-text fields are present
and non-blank.

Keeping the expectations in one dqflow `Contract` makes them inspectable
in a single place — dumpable to YAML with `LLM_RESPONSE_CONTRACT.to_yaml`,
diffable in CI with `dq diff` — rather than scattered across Pydantic
`Field(...)` args. `LLMOutput` still runs afterwards for type coercion,
the 500-char `reasoning` cap, and to produce the typed object the rest of
the pipeline consumes.
"""

from __future__ import annotations

import pandas as pd
from common.enums import Action
from dqflow import Column, Contract
from dqflow.result import ValidationResult

_ALLOWED_ACTIONS = tuple(a.value for a in Action)

# dqflow's `pattern` is a full-match regex over the non-null string values.
# `\s*\S[\s\S]*` == "at least one non-whitespace character somewhere", i.e.
# rejects "" and whitespace-only. The length cap stays on `LLMOutput`.
_NON_BLANK = r"\s*\S[\s\S]*"

LLM_RESPONSE_CONTRACT = Contract(
    name="llm_response",
    description="One raw /analyze model response (PROJECT.md Section 8.2).",
    columns={
        "action": Column(dtype=str, not_null=True, allowed=_ALLOWED_ACTIONS),
        "confidence": Column(dtype=float, not_null=True, min=0.0, max=1.0),
        "reasoning": Column(dtype=str, not_null=True, pattern=_NON_BLANK),
        "invalidation_condition": Column(dtype=str, not_null=True, pattern=_NON_BLANK),
    },
    # `key_indicators` is a list field: dqflow validates columnar scalars, not
    # nested lists, and `LLMOutput` already defaults it to `[]` when absent —
    # so it is deliberately left to the Pydantic layer.
)

# A failed dqflow check name -> the structural-validation reason string the
# rest of the pipeline (audit payloads, the notifier's Telegram relay) is
# already written against. When dqflow's dtype check fails it skips that
# column's other checks, so `dtype:<col>` has to carry the same meaning as
# the value checks it pre-empted. `column_exists` failures (a missing field)
# are shape problems -> `schema_invalid`, matching `LLMOutput`'s handling of
# a missing key.
_ACTION_CHECKS = frozenset({"dtype:action", "not_null:action", "allowed:action"})
_CONFIDENCE_CHECKS = frozenset(
    {"dtype:confidence", "not_null:confidence", "min:confidence", "max:confidence"}
)


def contract_failure_reason(result: ValidationResult) -> str:
    failed = {check.name for check in result.failed_checks}
    if failed & _ACTION_CHECKS:
        return "invalid_action"
    if failed & _CONFIDENCE_CHECKS:
        return "invalid_confidence"
    return "schema_invalid"


def check_llm_response(data: dict) -> str | None:
    """Validate one already-JSON-parsed response dict against
    `LLM_RESPONSE_CONTRACT`. Returns `None` when it passes, otherwise the
    structural-failure reason (`invalid_action` / `invalid_confidence` /
    `schema_invalid`).

    The dict is wrapped as a single-row DataFrame — the unit dqflow
    validates. Any engine-level error is treated as `schema_invalid` rather
    than propagating."""
    try:
        result = LLM_RESPONSE_CONTRACT.validate(pd.DataFrame([data]))
    except Exception:
        return "schema_invalid"
    if result.ok:
        return None
    return contract_failure_reason(result)
