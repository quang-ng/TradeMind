async def test_get_llm_config_requires_auth(client):
    response = await client.get("/config/llm")
    assert response.status_code == 401


async def test_get_llm_config_returns_env_defaults_with_no_override(client, auth_headers):
    response = await client.get("/config/llm", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "llm_provider": "anthropic",
        "anthropic_model": "claude-sonnet-5",
        "ollama_model": "llama3.2:3b",
        "ollama_temperature": 0.4,
    }


async def test_patch_llm_config_persists_partial_override_and_writes_audit_event(
    client, db_session_factory, auth_headers
):
    response = await client.patch(
        "/config/llm",
        headers=auth_headers,
        json={"llm_provider": "ollama", "ollama_temperature": 0.7},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["llm_provider"] == "ollama"
    assert body["ollama_temperature"] == 0.7
    # Untouched fields keep falling back to their env default.
    assert body["anthropic_model"] == "claude-sonnet-5"
    assert body["ollama_model"] == "llama3.2:3b"

    follow_up = await client.get("/config/llm", headers=auth_headers)
    assert follow_up.json() == body

    from common.db.models import AuditEvent
    from sqlalchemy import select

    async with db_session_factory() as session:
        events = (await session.execute(select(AuditEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "CONFIG_CHANGED"
    assert events[0].payload["scope"] == "llm"
    assert events[0].payload["changes"] == {"llm_provider": "ollama", "ollama_temperature": 0.7}


async def test_patch_llm_config_rejects_unknown_provider(client, auth_headers):
    response = await client.patch(
        "/config/llm", headers=auth_headers, json={"llm_provider": "openai"}
    )
    assert response.status_code == 422


async def test_patch_llm_config_rejects_empty_patch(client, auth_headers):
    response = await client.patch("/config/llm", headers=auth_headers, json={})
    assert response.status_code == 400
