from integrations.mcp_server import handle_mcp_message


def test_mcp_initialize_advertises_capabilities():
    response = handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
    )

    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "stockflow-mcp"
    assert "tools" in response["result"]["capabilities"]
    assert "resources" in response["result"]["capabilities"]
    assert "prompts" in response["result"]["capabilities"]


def test_mcp_lists_stockflow_tools_resources_and_prompts():
    tools = handle_mcp_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resources = handle_mcp_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    prompts = handle_mcp_message({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})

    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    resource_uris = {resource["uri"] for resource in resources["result"]["resources"]}
    prompt_names = {prompt["name"] for prompt in prompts["result"]["prompts"]}

    assert "get_demo_state" in tool_names
    assert "run_simulation_tick" in tool_names
    assert "approve_decision" in tool_names
    assert "stockflow://current-state" in resource_uris
    assert "stockflow://agents/reasoning-traces" in resource_uris
    assert "prepare_recruiter_demo_script" in prompt_names


def test_mcp_unknown_tool_returns_json_rpc_error():
    response = handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "missing_tool", "arguments": {}},
        }
    )

    assert response["id"] == 5
    assert response["error"]["code"] == -32601
