"""Rule 6 (PROJECT.md Section 9.1) — Historical Expectancy Filter.

Positive-expectancy plan Phase 6 / M5. Answers "does this *type* of setup
(regime + trade-score bucket) historically have an edge?" before the
portfolio/capital gates run, using the cohort's realized Expectancy(R) from
the closed-position journal (`ctx.expectancy`, pre-fetched by
`expectancy_state.load_expectancy_state`).

**Ships DISABLED (operator decision D4).** With `expectancy_filter_enabled`
off — the default — `check()` always passes; it never rejects a live trade.
What it always does, on or off, is produce a `build_expectancy_check()`
verdict that the evaluator threads into the `RISK_APPROVED`/`RISK_REJECTED`
audit payload, so weeks of "what the filter *would* have decided" shadow
data accumulate before the operator ever flips it on via `PATCH /config`.

The Risk Engine stays the final authority: this rule can only make a
decision *stricter* (reject what would otherwise pass), never approve
what another rule rejects — it is one short-circuiting gate among the
Section 9.1 set, nothing more.
"""

from dataclasses import dataclass
from decimal import Decimal

from common.enums import RejectionReason

from ..schemas import RuleContext
from .base import RuleViolation

# The verdict the filter reaches, independent of whether it is enforced.
ALLOW = "ALLOW"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"


@dataclass(frozen=True)
class ExpectancyCheck:
    """The shadow-mode record written on *every* evaluated entry signal
    (approved or rejected, filter on or off) — D4's "auditable from day
    one". Serialized into the decision's audit payload by the evaluator."""

    setup_key: str
    sample_size: int
    historical_expectancy_r: Decimal | None
    # What the filter concluded about the setup (ALLOW / INSUFFICIENT_DATA /
    # NEGATIVE_EXPECTANCY) — recorded regardless of enforcement.
    decision: str
    # Whether `expectancy_filter_enabled` was on for this decision, i.e.
    # whether a NEGATIVE_EXPECTANCY verdict actually blocked the trade.
    enforced: bool

    def as_payload(self) -> dict:
        return {
            "setup_key": self.setup_key,
            "sample_size": self.sample_size,
            "historical_expectancy_r": (
                str(self.historical_expectancy_r)
                if self.historical_expectancy_r is not None
                else None
            ),
            "decision": self.decision,
            "enforced": self.enforced,
        }


def _decision(ctx: RuleContext) -> str:
    exp = ctx.expectancy
    config = ctx.config
    if exp.expectancy_r is None or exp.sample_size < config.expectancy_min_sample_size:
        # Below the minimum sample the filter has no basis to act —
        # abstain. Absence of evidence is not evidence of a bad setup; the
        # vision doc's worked examples always show a *computed* negative
        # number, never "unknown".
        return INSUFFICIENT_DATA
    if exp.expectancy_r < config.expectancy_min_r:
        return NEGATIVE_EXPECTANCY
    return ALLOW


def build_expectancy_check(ctx: RuleContext) -> ExpectancyCheck:
    """The shadow verdict for this signal. Called by the evaluator on every
    evaluated entry, whatever `check()` returns, so the audit trail carries
    it even when the filter is disabled or the sample is too small."""
    return ExpectancyCheck(
        setup_key=ctx.expectancy.setup_key,
        sample_size=ctx.expectancy.sample_size,
        historical_expectancy_r=ctx.expectancy.expectancy_r,
        decision=_decision(ctx),
        enforced=ctx.config.expectancy_filter_enabled,
    )


def check(ctx: RuleContext) -> RuleViolation | None:
    """Rejects only when the filter is explicitly enabled *and* the setup
    has a statistically sufficient, proven-negative historical expectancy.
    Disabled (default), or an insufficient sample, or a non-negative
    expectancy → passes. The shadow verdict is recorded separately via
    `build_expectancy_check()` no matter what this returns."""
    if not ctx.config.expectancy_filter_enabled:
        return None
    if _decision(ctx) == NEGATIVE_EXPECTANCY:
        return RuleViolation(reason=RejectionReason.NEGATIVE_EXPECTANCY_SETUP)
    return None
