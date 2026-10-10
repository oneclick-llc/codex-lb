"""Owner forward headers over a real socket.

The other bridge integration tests post through the httpx ASGI transport,
which never serializes headers, so they cannot see what aiohttp refuses to
write or what the production HTTP parser strips from a header value.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.cli import _load_http_protocol_class
from app.core.openai.requests import ResponsesRequest
from app.modules.proxy.affinity import _CODEX_SELECTION_KEY_PREFIX
from app.modules.proxy.http_bridge_forwarding import (
    HTTP_BRIDGE_INTERNAL_FORWARD_PATH,
    HTTPBridgeForwardContext,
    build_owner_forward_headers,
    parse_forwarded_request,
)

pytestmark = pytest.mark.integration


async def _owner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Parse the forward the way the owner route does and echo the affinity key."""
    request = Request(scope, receive)
    payload = ResponsesRequest.model_validate(json.loads(await request.body()))
    forwarded, error = parse_forwarded_request(request.headers, payload=payload, current_instance="instance-b")
    if error is not None or forwarded is None:
        body: dict[str, Any] = {"status": error.status_code if error is not None else 500, "key": None}
    else:
        body = {"status": 200, "key": forwarded.context.original_affinity_key}
    await JSONResponse(body)(scope, receive, send)


@pytest.mark.parametrize(
    "key",
    [
        f"{_CODEX_SELECTION_KEY_PREFIX}:thread_header:scope-1:{'ab' * 32}",
        "sid\t123",
        "sid-123",
    ],
    ids=["internal-selection-key", "interior-tab", "plain"],
)
@pytest.mark.parametrize("unanchored", [False, True], ids=["legacy-signature", "v2-signature"])
async def test_owner_forward_carries_affinity_key_over_real_socket(key: str, unanchored: bool) -> None:
    config = uvicorn.Config(
        app=_owner_app,
        host="127.0.0.1",
        port=0,
        http=_load_http_protocol_class(),
        lifespan="off",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10.0):
            while not server.started:
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
        context = HTTPBridgeForwardContext(
            origin_instance="instance-a",
            target_instance="instance-b",
            codex_session_affinity=True,
            downstream_turn_state="http_turn_123",
            original_request_unanchored=unanchored,
            original_affinity_kind="thread_header",
            original_affinity_key=key,
        )
        headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{port}{HTTP_BRIDGE_INTERNAL_FORWARD_PATH}",
                json=payload.model_dump_for_forwarding(),
                headers=headers,
            ) as response:
                body = await response.json()
    finally:
        server.should_exit = True
        await serve_task

    assert body == {"status": 200, "key": key}
