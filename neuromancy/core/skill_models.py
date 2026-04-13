"""Pydantic data models for skills.

Defines the in-memory representation of a skill's manifest, template,
steps, run results, and test captures. YAML template parsing is handled
by :meth:`SkillTemplate.from_yaml` which translates the raw YAML mapping
into a typed recursive structure (steps can contain conditional blocks
that themselves contain steps).

The recursive union ``SkillStepNode = Union[SkillStep, SkillWhenBlock]``
is resolved via an explicit :meth:`model_rebuild` call so Pydantic can
validate nested when blocks to arbitrary depth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StepAction(str, Enum):
    """The two supported step action types in v1 skill templates."""

    tool_call = "tool_call"
    llm_call = "llm_call"


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------


class SkillInputSpec(BaseModel):
    """Specification for a single input a skill accepts."""

    model_config = {"extra": "forbid"}

    type: str
    description: Optional[str] = None
    required: bool = False
    enum: Optional[list[Any]] = None
    default: Optional[Any] = None


# ---------------------------------------------------------------------------
# Steps and when blocks — recursive union
# ---------------------------------------------------------------------------


class SkillStep(BaseModel):
    """A single executable step in a skill template.

    Either a ``tool_call`` (dispatches to a plugin tool) or an ``llm_call``
    (sends a rendered prompt to the LLM provider). Every step carries an
    ``id`` used to reference its output from subsequent steps and an
    optional ``terminate_if`` condition that short-circuits the skill.
    """

    model_config = {"extra": "forbid"}

    id: str
    action: StepAction
    terminate_if: Optional[str] = None

    # Fields for ``tool_call``
    tool: Optional[str] = None
    arguments: dict = Field(default_factory=dict)

    # Fields for ``llm_call``
    model: Optional[str] = None
    prompt: Optional[str] = None

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "SkillStep":
        if self.action == StepAction.tool_call:
            if not self.tool:
                raise ValueError(
                    f"step {self.id!r}: tool_call steps require a 'tool' field"
                )
        elif self.action == StepAction.llm_call:
            if not self.prompt:
                raise ValueError(
                    f"step {self.id!r}: llm_call steps require a 'prompt' field"
                )
        return self


class SkillWhenBlock(BaseModel):
    """A conditional branch inside a skill template.

    ``then_branch`` runs when the DSL ``condition`` evaluates truthy;
    ``else_branch`` runs otherwise. Either branch may contain more
    regular steps or further nested when blocks.
    """

    model_config = {"extra": "forbid"}

    condition: str
    then_branch: list["SkillStepNode"] = Field(default_factory=list)
    else_branch: list["SkillStepNode"] = Field(default_factory=list)


# The recursive union must be declared after both leaf types exist. Pydantic
# then needs ``model_rebuild`` to resolve the forward reference used inside
# SkillWhenBlock's field annotations.
SkillStepNode = Union[SkillStep, SkillWhenBlock]
SkillWhenBlock.model_rebuild()


# ---------------------------------------------------------------------------
# Template — top-level skill definition
# ---------------------------------------------------------------------------


class SkillTemplate(BaseModel):
    """The top-level schema for a skill template."""

    model_config = {"extra": "forbid"}

    name: str
    version: int = 1
    description: str = ""
    inputs: dict[str, SkillInputSpec] = Field(default_factory=dict)
    steps: list[SkillStepNode] = Field(default_factory=list)
    output: str = ""

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "SkillTemplate":
        """Parse a YAML string into a typed SkillTemplate.

        Raises ``ValueError`` for structural problems (non-mapping root,
        unknown step action, when condition not a string) and the Pydantic
        validation exception for field-level issues.
        """
        raw = yaml.safe_load(yaml_text)
        if not isinstance(raw, dict):
            raise ValueError("template root must be a mapping")

        inputs_raw = raw.get("inputs") or {}
        if not isinstance(inputs_raw, dict):
            raise ValueError("'inputs' must be a mapping")
        inputs = {k: SkillInputSpec.model_validate(v) for k, v in inputs_raw.items()}

        steps_raw = raw.get("steps") or []
        if not isinstance(steps_raw, list):
            raise ValueError("'steps' must be a list")
        steps = [_parse_step(s) for s in steps_raw]

        return cls(
            name=raw["name"],
            version=raw.get("version", 1),
            description=raw.get("description", ""),
            inputs=inputs,
            steps=steps,
            output=raw.get("output", ""),
        )


def _parse_step(raw: dict) -> SkillStepNode:
    """Convert a raw step dict from YAML into a typed step node.

    The discriminator is the presence of a ``when`` key: if set, the
    dict is a :class:`SkillWhenBlock`; otherwise it's a regular
    :class:`SkillStep`. Unknown action strings raise a clear error.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"step must be a mapping, got {type(raw).__name__}")

    if "when" in raw:
        condition = raw["when"]
        if not isinstance(condition, str):
            raise ValueError(
                f"'when' condition must be a string, got {type(condition).__name__}"
            )
        then_raw = raw.get("then") or []
        else_raw = raw.get("else") or []
        if not isinstance(then_raw, list) or not isinstance(else_raw, list):
            raise ValueError("'then' and 'else' must be lists of steps")
        return SkillWhenBlock(
            condition=condition,
            then_branch=[_parse_step(s) for s in then_raw],
            else_branch=[_parse_step(s) for s in else_raw],
        )

    action_str = raw.get("action")
    valid_actions = {a.value for a in StepAction}
    if action_str not in valid_actions:
        raise ValueError(
            f"unknown step action: {action_str!r} (valid: {sorted(valid_actions)})"
        )
    return SkillStep.model_validate(raw)


# ---------------------------------------------------------------------------
# Manifest — persistent metadata stored alongside the template
# ---------------------------------------------------------------------------


class SkillManifest(BaseModel):
    """Persistent manifest for an active skill.

    Stored as ``manifest.json`` alongside ``template.yaml`` in each skill's
    directory. ``extra: allow`` provides forward compatibility — a future
    version adding new fields won't cause older loaders to reject the file.
    """

    model_config = {"extra": "allow"}

    name: str
    version: int
    description: str
    created_at: datetime
    updated_at: datetime
    source_session: Optional[str] = None
    input_schema: dict = Field(default_factory=dict)
    run_count: int = 0
    success_rate: float = 0.0
    has_compiled: bool = False
    trust_this_skill: bool = False
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Run results and test captures
# ---------------------------------------------------------------------------


class SkillStepResult(BaseModel):
    """Result of executing one step inside a SkillRunner run."""

    step_id: str
    output: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    tokens_used: int = 0


class SkillRunResult(BaseModel):
    """Full record of a single skill execution."""

    skill_name: str
    skill_version: int
    inputs: dict
    steps: list[SkillStepResult] = Field(default_factory=list)
    branch_path: list[str] = Field(default_factory=list)
    final_output: str = ""
    succeeded: bool = False
    error: Optional[str] = None
    duration_ms: float = 0.0
    cost_usd: float = 0.0
    started_at: datetime = Field(default_factory=_utcnow)


class SkillTestCase(BaseModel):
    """A captured run used for empirical grounding of future refinements.

    Each successful (or failed) skill execution writes one of these to the
    skill's ``tests/`` directory, providing the evidence that v2's compile
    step will validate refined versions against.
    """

    timestamp: datetime = Field(default_factory=_utcnow)
    inputs: dict
    branch_path: list[str]
    final_output: str
    succeeded: bool
    duration_ms: float
    cost_usd: float


# ---------------------------------------------------------------------------
# LoadedSkill — runtime container bundling manifest + template + backing dir
# ---------------------------------------------------------------------------


@dataclass
class LoadedSkill:
    """A manifest + template pair with its backing directory on disk.

    Lives in :mod:`skill_models` rather than :mod:`skill_registry` so that
    :mod:`skill_runner` can import it without creating a circular
    dependency between the runner and the registry.
    """

    manifest: SkillManifest
    template: SkillTemplate
    dir: Path
