from agent import google_antigravity_adapter as ag


def test_antigravity_headers_use_supported_version_without_gemini_cli_identity_headers(monkeypatch):
    monkeypatch.delenv("HERMES_ANTIGRAVITY_VERSION", raising=False)
    monkeypatch.delenv("ANTIGRAVITY_VERSION", raising=False)
    getattr(ag, "_ANTIGRAVITY_VERSION_CACHE").update({"version": "2.0.1", "fetched_at": 0.0})

    headers = ag.get_antigravity_headers()

    assert "User-Agent" in headers
    assert "Antigravity/2.0.1" in headers["User-Agent"]
    assert "Antigravity/1.18.3" not in headers["User-Agent"]
    assert "X-Goog-Api-Client" not in headers
    assert "Client-Metadata" not in headers


def test_antigravity_runtime_endpoints_do_not_include_staging_sandboxes():
    endpoints = getattr(ag, "ANTIGRAVITY_ENDPOINT_FALLBACKS")

    assert endpoints == (getattr(ag, "ANTIGRAVITY_ENDPOINT_PROD"),)
    assert getattr(ag, "ANTIGRAVITY_ENDPOINT_DAILY") not in endpoints
    assert getattr(ag, "ANTIGRAVITY_ENDPOINT_AUTOPUSH") not in endpoints


def test_antigravity_version_can_be_overridden_for_fast_upgrades(monkeypatch):
    monkeypatch.setenv("HERMES_ANTIGRAVITY_VERSION", "2.0.2")

    headers = ag.get_antigravity_headers()

    assert "Antigravity/2.0.2" in headers["User-Agent"]


def test_wrap_antigravity_request_uses_agent_body_metadata(monkeypatch):
    monkeypatch.delenv("HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS", raising=False)
    wrapped = getattr(ag, "_wrap_antigravity_request")(
        project_id="test-project",
        model="gemini-3-flash",
        request={"contents": []},
    )

    assert wrapped["project"] == "test-project"
    assert wrapped["model"] == "gemini-3-flash"
    assert wrapped["requestType"] == "agent"
    assert wrapped["userAgent"] == "antigravity"
    assert "enabledCreditTypes" not in wrapped
    assert str(wrapped["requestId"]).startswith("agent-")
    assert wrapped["request"] == {"contents": []}


def test_google_one_ai_credits_are_explicitly_opted_into():
    wrapped = getattr(ag, "_wrap_antigravity_request")(
        project_id="test-project",
        model="claude-opus-4-6-thinking",
        request={"contents": []},
        use_google_one_ai_credits=True,
    )

    assert wrapped["enabledCreditTypes"] == ["GOOGLE_ONE_AI"]


def test_google_one_ai_credit_mode_defaults_to_plan_detection(monkeypatch):
    monkeypatch.delenv("HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS", raising=False)

    paid_ctx = ag.ProjectContext(
        tier_id="g1-ultra-tier",
        paid_tier_id="g1-ultra-tier",
        paid_tier_name="Google AI Ultra",
        google_one_ai_credit_amount=23737,
        google_one_ai_minimum_credit_amount=50,
        has_google_one_ai_credits=True,
    )
    free_ctx = ag.ProjectContext(tier_id="free-tier")

    assert getattr(ag, "_antigravity_google_one_ai_credits_mode")() == "auto"
    assert getattr(ag, "_antigravity_credit_attempts")(paid_ctx) == [True]
    assert getattr(ag, "_antigravity_credit_attempts")(free_ctx) == [False]


def test_google_one_ai_credit_mode_can_prefer_base_quota_or_disable(monkeypatch):
    monkeypatch.setenv("HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS", "fallback")
    assert getattr(ag, "_antigravity_credit_attempts")() == [False, True]

    monkeypatch.setenv("HERMES_ANTIGRAVITY_GOOGLE_ONE_AI_CREDITS", "0")
    assert getattr(ag, "_antigravity_credit_attempts")() == [False]


def test_antigravity_session_id_is_stable_and_opaque():
    stable = getattr(ag, "_stable_antigravity_session_id")

    first = stable("telegram:user@example.com:session-123")
    second = stable("telegram:user@example.com:session-123")

    assert first == second
    assert first.startswith("-hermes-")
    assert "user@example.com" not in first
    assert stable("") == ""


def test_google_one_ai_credit_fallback_only_for_capacity_errors():
    checker = getattr(ag, "_is_google_one_ai_credit_fallback_error")

    capacity = ag.CodeAssistError(
        "You have exhausted your capacity on this model",
        code="code_assist_capacity_exhausted",
        status_code=429,
        details={"reason": "MODEL_CAPACITY_EXHAUSTED", "status": "RESOURCE_EXHAUSTED"},
    )
    quota = ag.CodeAssistError(
        "unrelated quota exhausted",
        code="code_assist_rate_limited",
        status_code=429,
        details={"reason": "OTHER_LIMIT", "status": "RESOURCE_EXHAUSTED"},
    )

    assert checker(capacity) is True
    assert checker(quota) is False


def test_capacity_pacing_defaults_only_for_burst_limited_models(monkeypatch):
    monkeypatch.delenv("HERMES_ANTIGRAVITY_CAPACITY_PACING_SECONDS", raising=False)
    interval = getattr(ag, "_antigravity_capacity_pacing_interval")

    assert interval("claude-opus-4-6-thinking") == getattr(ag, "ANTIGRAVITY_DEFAULT_CAPACITY_PACING_SECONDS")
    assert interval("gpt-oss-120b-medium") == getattr(ag, "ANTIGRAVITY_DEFAULT_CAPACITY_PACING_SECONDS")
    assert interval("gemini-3.1-pro-high") == 0.0


def test_capacity_pacing_interval_can_be_overridden(monkeypatch):
    monkeypatch.setenv("HERMES_ANTIGRAVITY_CAPACITY_PACING_SECONDS", "0")

    assert getattr(ag, "_antigravity_capacity_pacing_interval")("claude-opus-4-6-thinking") == 0.0


def test_short_antigravity_capacity_errors_are_internal_retryable():
    checker = getattr(ag, "_is_short_antigravity_capacity_error")
    short = ag.CodeAssistError(
        "You have exhausted your capacity on this model",
        code="code_assist_rate_limited",
        status_code=429,
        retry_after=1.2,
        details={"status": "RESOURCE_EXHAUSTED", "message": "You have exhausted your capacity"},
    )
    long = ag.CodeAssistError(
        "You have exhausted your capacity on this model",
        code="code_assist_rate_limited",
        status_code=429,
        retry_after=3600,
        details={"status": "RESOURCE_EXHAUSTED", "message": "You have exhausted your capacity"},
    )

    assert checker(short) is True
    assert checker(long) is False


def test_antigravity_20_ui_models_have_backend_fallbacks():
    candidates = getattr(ag, "_antigravity_model_candidates")

    assert candidates("gemini-3.5-flash-high") == ["gemini-3-flash-agent"]
    assert candidates("gemini-3.5-flash-medium") == ["gemini-3-flash"]
    assert candidates("gemini-3.1-pro-high") == ["gemini-3.1-pro-low"]
    assert candidates("gemini-3.1-pro-low") == ["gemini-3.1-pro-low"]
    assert candidates("claude-sonnet-4-6-thinking") == ["claude-sonnet-4-6"]
    assert candidates("claude-opus-4-6-thinking") == ["claude-opus-4-6-thinking"]
    assert candidates("gpt-oss-120b") == ["gpt-oss-120b-medium"]


def test_gemini_31_pro_ui_tiers_infer_thinking_level_and_token_floor():
    merge = getattr(ag, "_merge_antigravity_thinking_config")
    max_tokens = getattr(ag, "_antigravity_effective_max_tokens")

    assert merge("gemini-3.1-pro-high", None) == {"thinkingLevel": "high"}
    assert merge("gemini-3.1-pro-low", None) == {"thinkingLevel": "low"}
    assert merge("gemini-3.1-pro-high", {"includeThoughts": True}) == {
        "thinkingLevel": "high",
        "includeThoughts": True,
    }
    assert merge("gemini-3.1-pro-high", {"thinkingLevel": "low"}) == {"thinkingLevel": "low"}
    assert max_tokens("gemini-3.1-pro-high", 16) == getattr(ag, "GEMINI_31_PRO_MIN_OUTPUT_TOKENS")
    assert max_tokens("gemini-3.1-pro-low", 16) == getattr(ag, "GEMINI_31_PRO_MIN_OUTPUT_TOKENS")
    assert max_tokens("gemini-3.5-flash-high", 16) == 16


def test_claude_antigravity_transform_normalizes_tools_and_system_instruction():
    request = {
        "model": "claude-sonnet-4.5",
        "system_instruction": "User system prompt",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "bad tool.name",
                    "description": "Do work",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "extra_body": {"cached_content": "legacy", "thinkingConfig": {"thinkingBudget": 1024}},
    }

    getattr(ag, "_apply_antigravity_request_transforms")(request, model="claude-sonnet-4.5")

    assert "model" not in request
    assert "extra_body" not in request
    assert request["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
    declarations = request["tools"][0]["functionDeclarations"]
    assert declarations[0]["name"] == "bad_tool_name"
    assert declarations[0]["parameters"]["type"] == "object"
    assert getattr(ag, "EMPTY_SCHEMA_PLACEHOLDER_NAME") in declarations[0]["parameters"]["properties"]
    assert getattr(ag, "EMPTY_SCHEMA_PLACEHOLDER_NAME") in declarations[0]["parameters"]["required"]
    system = request["systemInstruction"]
    assert system["role"] == "user"
    assert getattr(ag, "ANTIGRAVITY_SYSTEM_INSTRUCTION") in system["parts"][0]["text"]
    assert "User system prompt" in system["parts"][0]["text"]
    assert request["sessionId"].startswith("-")
    assert "contextWindowCompression" not in request


def test_antigravity_transform_can_opt_into_context_compression(monkeypatch):
    monkeypatch.setenv("HERMES_ANTIGRAVITY_CONTEXT_COMPRESSION", "1")
    gemini_request = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    gpt_request = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

    getattr(ag, "_apply_antigravity_request_transforms")(
        gemini_request,
        model="gemini-3.5-flash-high",
    )
    getattr(ag, "_apply_antigravity_request_transforms")(
        gpt_request,
        model="gpt-oss-120b-medium",
    )

    assert gemini_request["contextWindowCompression"] == {
        "triggerTokens": 100_000,
        "slidingWindow": {"targetTokens": 60_000},
    }
    assert gpt_request["contextWindowCompression"] == {
        "triggerTokens": 100_000,
        "slidingWindow": {"targetTokens": 60_000},
    }


def test_antigravity_transform_preserves_stable_hermes_session_id():
    request = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    session_id = getattr(ag, "_stable_antigravity_session_id")("hermes-session")
    request["sessionId"] = session_id

    getattr(ag, "_apply_antigravity_request_transforms")(
        request,
        model="gemini-3.5-flash-high",
    )

    assert request["sessionId"] == session_id


def test_claude_thinking_transform_sets_thinking_config_and_max_tokens():
    request = {
        "generationConfig": {"maxOutputTokens": 1024},
        "tools": [{"functionDeclarations": [{"name": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]}],
    }

    getattr(ag, "_apply_antigravity_request_transforms")(
        request,
        model="claude-sonnet-4.5-thinking",
        thinking_config={"thinkingBudget": 2048, "includeThoughts": True},
    )

    gen = request["generationConfig"]
    assert gen["thinkingConfig"] == {"include_thoughts": True, "thinking_budget": 2048}
    assert gen["maxOutputTokens"] == 64_000
    assert request["tools"][0]["functionDeclarations"][0]["parameters"]["required"] == [getattr(ag, "EMPTY_SCHEMA_PLACEHOLDER_NAME")]
    assert "Interleaved thinking is enabled" in request["systemInstruction"]["parts"][0]["text"]


def test_claude_thinking_transform_can_disable_thought_inclusion():
    request = {"generationConfig": {"maxOutputTokens": 4096}}

    getattr(ag, "_apply_antigravity_request_transforms")(
        request,
        model="claude-opus-4-6-thinking",
        thinking_config={"includeThoughts": False},
    )

    assert request["generationConfig"]["thinkingConfig"] == {"include_thoughts": False}
    assert request["generationConfig"]["maxOutputTokens"] == 4096


def test_claude_tool_schema_keeps_optional_only_objects_draft_compatible():
    schema = getattr(ag, "_normalize_claude_schema")({
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["send", "list"]},
            "target": {"type": "string"},
            "message": {"type": "string"},
        },
    })

    assert schema["type"] == "object"
    assert schema["required"] == [getattr(ag, "EMPTY_SCHEMA_PLACEHOLDER_NAME")]
    assert getattr(ag, "EMPTY_SCHEMA_PLACEHOLDER_NAME") in schema["properties"]


def test_claude_tool_schema_simplifies_mcp_union_items():
    schema = getattr(ag, "_normalize_claude_schema")({
        "type": "object",
        "properties": {
            "comments": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "object", "properties": {"path": {"type": "string"}, "position": {"type": "number"}}, "required": ["path", "position"]},
                        {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "number"}}, "required": ["path", "line"]},
                    ]
                },
            }
        },
        "required": ["comments"],
    })

    items = schema["properties"]["comments"]["items"]
    assert "anyOf" not in items
    assert items["type"] == "object"
    assert "position" in items["properties"]


def test_gpt_oss_transform_strips_proto_stringified_numeric_schema_constraints():
    request = {
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": "clarify",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "minLength": 1},
                                "choices": {
                                    "type": "array",
                                    "items": {"type": "string", "maxLength": 32},
                                    "maxItems": 4,
                                    "minItems": 1,
                                },
                            },
                            "required": ["question"],
                        },
                    }
                ]
            }
        ]
    }

    getattr(ag, "_apply_antigravity_request_transforms")(request, model="gpt-oss-120b")

    params = request["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "minLength" not in params["properties"]["question"]
    assert "maxItems" not in params["properties"]["choices"]
    assert "minItems" not in params["properties"]["choices"]
    assert "maxLength" not in params["properties"]["choices"]["items"]
    assert params["properties"]["choices"]["type"] == "array"
    assert params["required"] == ["question"]


def test_invalid_argument_retry_body_can_drop_request_metadata_for_poisoned_fallbacks():
    wrapped = {
        "project": "test-project",
        "model": "gemini-3-flash-agent",
        "requestId": "agent-old",
        "request": {
            "contents": [
                {"role": "user", "parts": [{"text": "old"}]},
                {"role": "model", "parts": [{"functionCall": {"name": "read", "args": {}}}]},
                {"role": "user", "parts": [{"functionResponse": {"name": "read", "response": {"content": "secret-ish"}}}]},
                {"role": "user", "parts": [{"text": "latest request"}]},
            ],
            "systemInstruction": {"role": "user", "parts": [{"text": "large poisoned system"}]},
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}},
            "sessionId": "sticky-session",
            "tools": [{"functionDeclarations": [{"name": "read"}]}],
        },
    }

    retry = getattr(ag, "_minimal_invalid_argument_retry_body")(
        wrapped,
        preserve_request_metadata=False,
    )

    assert retry["requestId"] != "agent-old"
    assert retry["request"] == {
        "contents": [{"role": "user", "parts": [{"text": "latest request"}]}],
        "systemInstruction": {"role": "user", "parts": [{"text": "large poisoned system"}]},
    }
    assert retry["project"] == "test-project"
    assert retry["model"] == "gemini-3-flash-agent"


def test_invalid_argument_metadata_preserving_retry_drops_tools():
    wrapped = {
        "project": "test-project",
        "model": "gemini-3-flash-agent",
        "requestId": "agent-old",
        "request": {
            "contents": [
                {"role": "user", "parts": [{"text": "old"}]},
                {"role": "user", "parts": [{"text": "latest request"}]},
            ],
            "systemInstruction": {"role": "user", "parts": [{"text": "system"}]},
            "generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}},
            "sessionId": "sticky-session",
            "contextWindowCompression": {"triggerTokens": 100000, "slidingWindow": {"targetTokens": 60000}},
            "tools": [{"functionDeclarations": [{"name": "read"}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        },
    }

    retry = getattr(ag, "_minimal_invalid_argument_retry_body")(wrapped)

    assert retry["requestId"] != "agent-old"
    assert "contextWindowCompression" not in retry["request"]
    assert "tools" not in retry["request"]
    assert "toolConfig" not in retry["request"]
    assert retry["request"]["systemInstruction"] == wrapped["request"]["systemInstruction"]
    assert retry["request"]["generationConfig"] == wrapped["request"]["generationConfig"]
    assert retry["request"]["sessionId"] == "sticky-session"
