"""Integration tests for the interactive API endpoints.

Drives the real `InteractiveService` and the real `ConversationRegistry`
on disk under `tmp_rdst_home`. The send-message path requires an LLM
boundary mock and is left for a follow-up file — what we cover here is
the read/delete surface, which doesn't.
"""

from __future__ import annotations

UNKNOWN_HASH = "deadbeefdeadbeefdeadbeefdeadbeef"


async def test_status_for_unknown_query_hash_reports_not_exists(
    client, tmp_rdst_home
):
    """Status returns 200 with `exists=false` (not 404) when no
    conversation has been recorded for the hash."""
    response = await client.get(f"/api/interactive/{UNKNOWN_HASH}/status")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["exists"] is False
    assert body["conversation_id"] is None
    assert body["message_count"] is None


async def test_history_empty_when_no_conversation_exists(client, tmp_rdst_home):
    """History returns 200 with an empty `messages` list for an unknown
    hash — we never auto-create a conversation just to read history."""
    response = await client.get(f"/api/interactive/{UNKNOWN_HASH}/history")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body == {"messages": []}


async def test_delete_unknown_conversation_returns_success_false(
    client, tmp_rdst_home
):
    """Deleting a non-existent conversation reports `success=false`
    (the registry returns False because nothing was deleted)."""
    response = await client.delete(f"/api/interactive/{UNKNOWN_HASH}")
    assert response.status_code == 200, response.text
    assert response.json() == {"success": False}
