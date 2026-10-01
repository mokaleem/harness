"""Where an extension's middleware sits in the host's middleware stack.

Placement is declared as a *semantic guarantee* ("I need to observe the raw
tool return") rather than as a structural position ("put me in layer 3"). A
middleware occupies one index in the list, but that index only has meaning on
the hook chain it actually implements — so "outermost" means different things
on the model axis and the tool axis. Declaring by axis-and-end removes that
ambiguity and keeps the host free to restructure its stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, StrEnum, auto
from typing import Any

from deerflow_extension_api.contracts import HostPolicySnapshot


class Placement(StrEnum):
    MODEL_LOGICAL = "model_logical"
    """Model axis, outer end. Guarantee: outer of retry and error handling.
    Fires once per logical decision regardless of how many times the host
    retries underneath."""

    MODEL_PHYSICAL = "model_physical"
    """Model axis, inner end. Guarantee: inner of every request-transforming
    middleware. Fires once per physical provider call; retries re-enter it."""

    TOOL_VISIBLE = "tool_visible"
    """Tool axis, outer end. Guarantee: outer of truncation, sanitization and
    error wrapping. Observes what the model finally sees."""

    TOOL_RAW = "tool_raw"
    """Tool axis, inner end. Guarantee: adjacent to the real callable
    boundary. Observes the tool's raw return before any processing."""

    STANDARD = "standard"
    """No before/after-processing requirement. Relative order against other
    STANDARD contributors is not guaranteed."""


class AgentScope(Flag):
    LEAD = auto()
    SUBAGENT = auto()
    BOTH = LEAD | SUBAGENT


@dataclass(frozen=True)
class AgentBuildContext:
    """What an extension may know while deciding what to contribute."""

    scope: AgentScope
    agent_name: str | None = None
    model_name: str | None = None
    policy: HostPolicySnapshot = field(default_factory=HostPolicySnapshot)


@dataclass(frozen=True)
class MiddlewarePlacement:
    """One middleware plus where it needs to sit.

    ``middleware`` is typed ``Any`` rather than ``AgentMiddleware`` so this
    module stays import-light; the host validates the type at injection time.
    """

    middleware: Any
    placement: Placement
    scope: AgentScope = AgentScope.BOTH
    order: int = 0
    execution: bool = False
    """Trusted tool interceptor: preserve returns and propagate failures.

    Default contributions remain observational. Execution requires TOOL_RAW;
    the original admitted request is retained across every downstream call.
    """
    max_handler_calls: int = 1

    def __post_init__(self):
        if type(self.execution) is not bool or type(self.max_handler_calls) is not int or not 1 <= self.max_handler_calls <= 4:
            raise ValueError("execution must be boolean and max_handler_calls between 1 and 4")
        if self.execution and self.placement != Placement.TOOL_RAW:
            raise ValueError("Execution contributions require TOOL_RAW placement")
        if not self.execution and self.max_handler_calls != 1:
            raise ValueError("Observational contributions permit one handler call")
