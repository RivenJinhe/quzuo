import json
from unittest.mock import Mock

import httpx
import pytest

from app.models import Generation
from app.services import generate


@pytest.fixture
def run_model(monkeypatch):
    """用内存中的厂商响应驱动生成逻辑。"""
    monkeypatch.setattr(generate.settings, "llm_allow_real", True)
    monkeypatch.setattr(generate.providers, "api_key", lambda spec: "test")
    payload = {
        "task_type": "review", "goal": "准备复习", "materials": [],
        "conditions": {}, "retained": {}, "pending_facts": {},
    }

    def run(responses, model="deepseek/deepseek-chat"):
        post = Mock(side_effect=responses)
        monkeypatch.setattr(generate, "_post_chat", post)
        return generate.generate(payload, model)

    return run


def reply(content, reason="stop", tokens=1):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": reason}],
        "usage": {"prompt_tokens": 2, "completion_tokens": tokens},
    })


@pytest.mark.parametrize("model", ["deepseek/deepseek-chat", "minimax/MiniMax-M1"])
def test_missing_comma_regenerates_and_counts_both_replies(run_model, model):
    result, meta = run_model([
        reply('{"headline":"复习" "items":[]}', tokens=6),
        reply('{"headline":"复习","items":[]}', tokens=5),
    ], model)
    assert result == {"headline": "复习", "items": []}
    assert meta["usage"] == {"prompt_tokens": 4, "completion_tokens": 11, "total_tokens": 15}


def test_truncated_reply_is_regenerated_even_if_it_looks_complete(run_model):
    result, _ = run_model([reply('{"headline":"初稿"}', "length"), reply('{"headline":"完整方案"}')])
    assert result == {"headline": "完整方案"}


def test_two_invalid_replies_record_model_error_without_a_result(run_model):
    with pytest.raises(generate.ModelOutputError) as raised:
        run_model([reply(""), reply('{"items":[')])
    assert isinstance(raised.value.__cause__, json.JSONDecodeError)

    generation = Generation(model="deepseek/deepseek-chat")
    generate._fail_generation(Mock(), None, generation, raised.value)
    assert generation.status == "failed"
    assert generation.error_code == "MODEL_OUTPUT_INVALID"
    assert generation.result is None
    assert generation.usage["total_tokens"] == 6


@pytest.mark.parametrize("content", ['[{"items":[]}]', '{"items":[]}{"items":', None])
def test_invalid_content_cannot_be_used_as_a_plan(content):
    with pytest.raises(generate.ModelOutputError):
        generate._extract_json(content)


def test_fenced_plan_preserves_backticks_in_quoted_material(run_model):
    result, meta = run_model([reply('```json\n{"excerpt":"用 ``` 标记代码"}\n```')])
    assert result == {"excerpt": "用 ``` 标记代码"}
    assert meta["usage"]["total_tokens"] == 3


def test_provider_errors_do_not_trigger_format_retries(run_model):
    with pytest.raises(generate.ProviderResponseError, match="HTTP 429"):
        run_model([httpx.Response(429)])
