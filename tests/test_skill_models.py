"""Tests for Pydantic skill data models and YAML parsing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from neuromancy.core.skill_models import (
    LoadedSkill,
    SkillInputSpec,
    SkillManifest,
    SkillRunResult,
    SkillStep,
    SkillStepResult,
    SkillTemplate,
    SkillTestCase,
    SkillWhenBlock,
    StepAction,
)


# ---------------------------------------------------------------------------
# SkillManifest
# ---------------------------------------------------------------------------


def _minimal_manifest_data() -> dict:
    return {
        "name": "hello",
        "version": 1,
        "description": "say hi",
        "created_at": "2026-04-13T00:00:00+00:00",
        "updated_at": "2026-04-13T00:00:00+00:00",
        "input_schema": {"type": "object", "properties": {}},
    }


def test_manifest_loads_minimal_required_fields():
    manifest = SkillManifest.model_validate(_minimal_manifest_data())
    assert manifest.name == "hello"
    assert manifest.version == 1
    assert manifest.description == "say hi"
    assert manifest.run_count == 0
    assert manifest.success_rate == 0.0
    assert manifest.has_compiled is False
    assert manifest.trust_this_skill is False
    assert manifest.tags == []


def test_manifest_accepts_optional_fields():
    data = _minimal_manifest_data()
    data.update({
        "source_session": "sess-42",
        "run_count": 5,
        "success_rate": 0.8,
        "has_compiled": True,
        "trust_this_skill": True,
        "tags": ["research", "web"],
    })
    manifest = SkillManifest.model_validate(data)
    assert manifest.source_session == "sess-42"
    assert manifest.run_count == 5
    assert manifest.success_rate == 0.8
    assert manifest.has_compiled is True
    assert manifest.trust_this_skill is True
    assert manifest.tags == ["research", "web"]


def test_manifest_tolerates_unknown_fields_for_forward_compat():
    """If a future version adds fields, the current schema shouldn't reject them."""
    data = _minimal_manifest_data()
    data["future_field"] = "something"
    # Should not raise
    manifest = SkillManifest.model_validate(data)
    assert manifest.name == "hello"


def test_manifest_rejects_missing_required_field():
    data = _minimal_manifest_data()
    del data["name"]
    with pytest.raises(Exception):
        SkillManifest.model_validate(data)


def test_manifest_preserves_datetime():
    data = _minimal_manifest_data()
    manifest = SkillManifest.model_validate(data)
    assert isinstance(manifest.created_at, datetime)
    assert manifest.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# SkillTemplate — YAML parsing
# ---------------------------------------------------------------------------


def test_template_parses_minimal_skill():
    yaml_text = """
name: minimal
version: 1
description: does nothing
inputs: {}
steps: []
output: ""
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert template.name == "minimal"
    assert template.version == 1
    assert template.steps == []
    assert template.inputs == {}


def test_template_parses_sequential_tool_and_llm_steps():
    yaml_text = """
name: research
version: 1
description: research a topic
inputs:
  topic:
    type: string
    required: true
steps:
  - id: search
    action: tool_call
    tool: web_search
    arguments:
      query: "{{ inputs.topic }}"
      limit: 5
  - id: summarize
    action: llm_call
    model: anthropic/claude-haiku-4
    prompt: "Summarize: {{ steps.search.output }}"
output: "{{ steps.summarize.output }}"
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert template.name == "research"
    assert len(template.steps) == 2

    search = template.steps[0]
    assert isinstance(search, SkillStep)
    assert search.id == "search"
    assert search.action == StepAction.tool_call
    assert search.tool == "web_search"
    assert search.arguments == {"query": "{{ inputs.topic }}", "limit": 5}

    summarize = template.steps[1]
    assert isinstance(summarize, SkillStep)
    assert summarize.action == StepAction.llm_call
    assert summarize.model == "anthropic/claude-haiku-4"
    assert "Summarize" in summarize.prompt


def test_template_parses_input_specs():
    yaml_text = """
name: skill
version: 1
description: ""
inputs:
  topic:
    type: string
    description: What to research
    required: true
  depth:
    type: string
    enum: [shallow, deep]
    default: shallow
steps: []
output: ""
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert "topic" in template.inputs
    assert template.inputs["topic"].type == "string"
    assert template.inputs["topic"].required is True
    assert template.inputs["topic"].description == "What to research"
    assert template.inputs["depth"].enum == ["shallow", "deep"]
    assert template.inputs["depth"].default == "shallow"
    assert template.inputs["depth"].required is False


def test_template_parses_when_block_with_then_and_else():
    yaml_text = """
name: branching
version: 1
description: demo when block
inputs: {}
steps:
  - id: step_a
    action: tool_call
    tool: web_search
    arguments:
      query: "x"
  - when: "steps.step_a.output.results.length == 0"
    then:
      - id: fallback
        action: llm_call
        prompt: "fallback"
    else:
      - id: happy
        action: llm_call
        prompt: "happy path"
output: "{{ steps.happy.output }}"
"""
    template = SkillTemplate.from_yaml(yaml_text)
    assert len(template.steps) == 2

    step_a = template.steps[0]
    assert isinstance(step_a, SkillStep)
    assert step_a.id == "step_a"

    when_block = template.steps[1]
    assert isinstance(when_block, SkillWhenBlock)
    assert when_block.condition == "steps.step_a.output.results.length == 0"
    assert len(when_block.then_branch) == 1
    assert len(when_block.else_branch) == 1
    assert when_block.then_branch[0].id == "fallback"
    assert when_block.else_branch[0].id == "happy"


def test_template_parses_when_block_with_only_then():
    """A when block without an else branch is still valid."""
    yaml_text = """
name: one_sided
version: 1
description: ""
inputs: {}
steps:
  - when: "inputs.topic == \\"python\\""
    then:
      - id: only_on_python
        action: llm_call
        prompt: "python-specific"
output: ""
"""
    template = SkillTemplate.from_yaml(yaml_text)
    when_block = template.steps[0]
    assert isinstance(when_block, SkillWhenBlock)
    assert len(when_block.then_branch) == 1
    assert when_block.else_branch == []


def test_template_parses_nested_when_blocks():
    """When blocks inside then/else branches should parse correctly."""
    yaml_text = """
name: nested
version: 1
description: ""
inputs: {}
steps:
  - when: "inputs.outer == true"
    then:
      - when: "inputs.inner == true"
        then:
          - id: deep
            action: llm_call
            prompt: "deep"
        else:
          - id: shallow_inner
            action: llm_call
            prompt: "shallow inner"
    else:
      - id: outer_else
        action: llm_call
        prompt: "outer else"
output: ""
"""
    template = SkillTemplate.from_yaml(yaml_text)
    outer_when = template.steps[0]
    assert isinstance(outer_when, SkillWhenBlock)
    inner_when = outer_when.then_branch[0]
    assert isinstance(inner_when, SkillWhenBlock)
    assert len(inner_when.then_branch) == 1
    assert inner_when.then_branch[0].id == "deep"


def test_template_parses_terminate_if_on_regular_step():
    yaml_text = """
name: early_exit
version: 1
description: ""
inputs: {}
steps:
  - id: first
    action: llm_call
    prompt: "first"
  - id: maybe_last
    terminate_if: "steps.first.output != \\"\\""
    action: llm_call
    prompt: "maybe"
output: ""
"""
    template = SkillTemplate.from_yaml(yaml_text)
    maybe = template.steps[1]
    assert isinstance(maybe, SkillStep)
    assert maybe.terminate_if == 'steps.first.output != ""'


# ---------------------------------------------------------------------------
# SkillTemplate — rejection cases
# ---------------------------------------------------------------------------


def test_template_rejects_unknown_action():
    yaml_text = """
name: bad
version: 1
description: ""
inputs: {}
steps:
  - id: step
    action: teleport
output: ""
"""
    with pytest.raises(ValueError, match="unknown step action"):
        SkillTemplate.from_yaml(yaml_text)


def test_template_rejects_tool_call_without_tool_field():
    yaml_text = """
name: bad
version: 1
description: ""
inputs: {}
steps:
  - id: step
    action: tool_call
output: ""
"""
    with pytest.raises(Exception):  # Pydantic ValidationError
        SkillTemplate.from_yaml(yaml_text)


def test_template_rejects_llm_call_without_prompt_field():
    yaml_text = """
name: bad
version: 1
description: ""
inputs: {}
steps:
  - id: step
    action: llm_call
output: ""
"""
    with pytest.raises(Exception):
        SkillTemplate.from_yaml(yaml_text)


def test_template_rejects_non_mapping_root():
    """YAML that isn't a dict at the root level should raise clearly."""
    with pytest.raises(ValueError, match="root must be a mapping"):
        SkillTemplate.from_yaml("- just a list\n- of items\n")


def test_template_rejects_when_block_with_non_string_condition():
    yaml_text = """
name: bad
version: 1
description: ""
inputs: {}
steps:
  - when: 123
    then: []
output: ""
"""
    with pytest.raises(ValueError, match="condition must be a string"):
        SkillTemplate.from_yaml(yaml_text)


def test_template_rejects_missing_name_field():
    yaml_text = """
version: 1
description: ""
inputs: {}
steps: []
output: ""
"""
    with pytest.raises(Exception):
        SkillTemplate.from_yaml(yaml_text)


# ---------------------------------------------------------------------------
# SkillStep direct instantiation
# ---------------------------------------------------------------------------


def test_skill_step_tool_call_requires_tool_field():
    with pytest.raises(Exception):
        SkillStep(id="s", action=StepAction.tool_call)


def test_skill_step_llm_call_requires_prompt_field():
    with pytest.raises(Exception):
        SkillStep(id="s", action=StepAction.llm_call)


def test_skill_step_valid_tool_call():
    step = SkillStep(
        id="s",
        action=StepAction.tool_call,
        tool="web_search",
        arguments={"query": "x"},
    )
    assert step.tool == "web_search"
    assert step.arguments == {"query": "x"}


def test_skill_step_valid_llm_call():
    step = SkillStep(id="s", action=StepAction.llm_call, prompt="do it")
    assert step.prompt == "do it"


def test_skill_step_terminate_if_is_optional():
    step = SkillStep(id="s", action=StepAction.llm_call, prompt="x")
    assert step.terminate_if is None


# ---------------------------------------------------------------------------
# SkillRunResult
# ---------------------------------------------------------------------------


def test_skill_run_result_serialization_roundtrip():
    original = SkillRunResult(
        skill_name="research",
        skill_version=1,
        inputs={"topic": "python"},
        steps=[
            SkillStepResult(step_id="search", output={"results": ["a", "b"]}, duration_ms=123.0),
            SkillStepResult(step_id="summarize", output="final text", duration_ms=456.0),
        ],
        branch_path=["search", "summarize"],
        final_output="final text",
        succeeded=True,
        duration_ms=579.0,
        cost_usd=0.012,
    )
    data = original.model_dump(mode="json")
    restored = SkillRunResult.model_validate(data)
    assert restored.skill_name == "research"
    assert restored.succeeded is True
    assert restored.branch_path == ["search", "summarize"]
    assert len(restored.steps) == 2
    assert restored.steps[0].output == {"results": ["a", "b"]}


def test_skill_run_result_defaults_to_not_succeeded():
    result = SkillRunResult(
        skill_name="x",
        skill_version=1,
        inputs={},
    )
    assert result.succeeded is False
    assert result.steps == []
    assert result.branch_path == []
    assert result.error is None


def test_skill_step_result_accepts_arbitrary_output_types():
    """Step output can be a string, dict, list, number — anything JSON-serializable."""
    r1 = SkillStepResult(step_id="s", output="plain string")
    r2 = SkillStepResult(step_id="s", output={"structured": True})
    r3 = SkillStepResult(step_id="s", output=[1, 2, 3])
    r4 = SkillStepResult(step_id="s", output=42)
    assert r1.output == "plain string"
    assert r2.output == {"structured": True}
    assert r3.output == [1, 2, 3]
    assert r4.output == 42


# ---------------------------------------------------------------------------
# SkillTestCase
# ---------------------------------------------------------------------------


def test_skill_test_case_auto_timestamps():
    case = SkillTestCase(
        inputs={"topic": "x"},
        branch_path=["a", "b"],
        final_output="done",
        succeeded=True,
        duration_ms=100.0,
        cost_usd=0.001,
    )
    assert isinstance(case.timestamp, datetime)
    assert case.timestamp.tzinfo is not None


def test_skill_test_case_serialization_roundtrip():
    case = SkillTestCase(
        inputs={"k": "v"},
        branch_path=["x"],
        final_output="y",
        succeeded=True,
        duration_ms=1.0,
        cost_usd=0.0,
    )
    data = case.model_dump(mode="json")
    restored = SkillTestCase.model_validate(data)
    assert restored.inputs == {"k": "v"}
    assert restored.branch_path == ["x"]


# ---------------------------------------------------------------------------
# LoadedSkill container
# ---------------------------------------------------------------------------


def test_loaded_skill_container(tmp_path: Path):
    manifest = SkillManifest.model_validate(_minimal_manifest_data())
    template = SkillTemplate.from_yaml(
        "name: hello\nversion: 1\ndescription: x\ninputs: {}\nsteps: []\noutput: ''\n"
    )
    loaded = LoadedSkill(manifest=manifest, template=template, dir=tmp_path)
    assert loaded.manifest.name == "hello"
    assert loaded.template.name == "hello"
    assert loaded.dir == tmp_path


# ---------------------------------------------------------------------------
# SkillInputSpec
# ---------------------------------------------------------------------------


def test_input_spec_minimal():
    spec = SkillInputSpec(type="string")
    assert spec.type == "string"
    assert spec.required is False
    assert spec.default is None


def test_input_spec_with_enum():
    spec = SkillInputSpec(type="string", enum=["a", "b", "c"], default="a")
    assert spec.enum == ["a", "b", "c"]
    assert spec.default == "a"
