import json
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from app.models import Generation
from app.services import generate


@pytest.fixture
def post(monkeypatch):
    transport = Mock()
    monkeypatch.setattr(generate, "_post_chat", transport)
    return transport


@pytest.fixture
def run_model(monkeypatch, post):
    """用内存中的厂商响应驱动生成逻辑。"""
    monkeypatch.setattr(generate.settings, "llm_allow_real", True)
    monkeypatch.setattr(generate.providers, "api_key", lambda spec: "test")

    def run(responses, model="deepseek/deepseek-chat", previous=None):
        post.side_effect = responses
        return generate.generate({
            "task_type": "review", "goal": "准备复习", "materials": [],
            "conditions": {}, "retained": {}, "pending_facts": {}, "previous_result": previous,
        }, model)

    return run


PLAN = {
    "task_type": "review", "headline": "课前复习",
    "items": [{"title": "回顾", "minutes": 5, "detail": "解释代码标记",
               "citations": [{"material_id": "notes", "excerpt": "用 ``` 标记代码"}]}],
}


def call(arguments=None, name="submit_plan"):
    return {"type": "function", "function": {
        "name": name, "arguments": json.dumps(PLAN) if arguments is None else arguments,
    }}


def reply(calls, reason="tool_calls", tokens=1, content=None):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content, "tool_calls": calls}, "finish_reason": reason}],
        "usage": {"prompt_tokens": 2, "completion_tokens": tokens},
    })


@pytest.mark.parametrize(("model", "choice"), [
    ("deepseek/deepseek-chat", {"type": "function", "function": {"name": "submit_plan"}}),
    ("kimi/kimi-k2.6", "auto"),
    ("qwen/qwen-plus", "auto"),
    ("minimax/MiniMax-M1", "auto"),
])
def test_submission_uses_declared_tool_arguments(run_model, post, model, choice):
    result, meta = run_model([reply([call()], content="其他说明")], model)
    body = post.call_args.args[2]
    tool, = body["tools"]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "submit_plan"
    assert tool["function"]["parameters"]["type"] == "object"
    assert body["tool_choice"] == choice
    assert "response_format" not in body
    assert result == PLAN
    assert meta["usage"]["total_tokens"] == 3


def test_revision_submits_new_plan_using_previous_context(run_model, post):
    previous = {"body": "先复习第八页"}
    result, _ = run_model([reply([call()])], previous=previous)
    body = post.call_args.args[2]
    assert previous["body"] in body["messages"][-1]["content"]
    assert result == PLAN


@pytest.mark.parametrize("first", [
    reply(None, reason="stop", content=json.dumps(PLAN)),
    reply([]),
    reply([call(name="search_materials")]),
    reply([call(), call()]),
    reply([{"type": "function", "function": {"name": "submit_plan"}}]),
    reply([call('{"headline":"复习" "items":[]}')]),
    reply([call("[]")]),
    reply([call(json.dumps({**PLAN, "items": "无"}))]),
    reply([call()], reason="length"),
])
def test_invalid_submission_retries_once_and_counts_both_replies(run_model, first):
    result, meta = run_model([first, reply([call()], tokens=5)])
    assert result == PLAN
    assert meta["usage"] == {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}


def test_two_invalid_submissions_record_error_without_a_result(run_model):
    with pytest.raises(generate.ModelOutputError) as raised:
        run_model([reply([]), reply([call('{"items":[')])])
    assert isinstance(raised.value.__cause__, ValidationError)

    generation = Generation(model="deepseek/deepseek-chat")
    generate._fail_generation(Mock(), None, generation, raised.value)
    assert generation.status == "failed"
    assert generation.error_code == "MODEL_OUTPUT_INVALID"
    assert generation.result is None
    assert generation.usage["total_tokens"] == 6


def test_provider_errors_do_not_trigger_format_retries(run_model, post):
    with pytest.raises(generate.ProviderResponseError, match="HTTP 429"):
        run_model([httpx.Response(429)])
    assert post.call_count == 1
