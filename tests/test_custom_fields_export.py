"""Tests for custom field labels and ticket export."""

import pytest

from zendesk_skill import export, operations


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, endpoint, params=None, timeout=None):
        self.calls.append(endpoint)
        if endpoint == "ticket_fields.json":
            return {"ticket_fields": [
                {"id": 100, "title": "Product", "type": "tagger",
                 "custom_field_options": [{"name": "Widget Pro", "value": "widget_pro"}]},
                {"id": 101, "title": "Affected OS", "type": "multiselect",
                 "custom_field_options": [{"name": "macOS", "value": "mac"}, {"name": "Windows", "value": "win"}]},
                {"id": 102, "title": "Order #", "type": "text", "custom_field_options": []},
            ]}
        if endpoint == "users/show_many.json":
            return {"users": [
                {"id": 1, "name": "Alice <b>", "email": "alice@example.com"},
                {"id": 2, "name": "Bob", "email": "bob@brightstar.test"},
                {"id": 3, "name": "Cara CC", "email": "cara@example.com"},
            ]}
        if endpoint == "tickets/7.json":
            return {
                "ticket": {
                    "id": 7, "subject": "Broken <script>", "status": "open", "priority": "high",
                    "requester_id": 1, "assignee_id": 2, "organization_id": 9, "group_id": 8,
                    "email_cc_ids": [3], "follower_ids": [2], "collaborator_ids": [3, 2],
                    "tags": ["a", "b"], "url": "https://x.zendesk.com/api/v2/tickets/7.json",
                    "custom_fields": [
                        {"id": 100, "value": "widget_pro"},
                        {"id": 101, "value": ["mac", "win"]},
                        {"id": 102, "value": "A-123"},
                        {"id": 999, "value": "unknown def"},
                        {"id": 102, "value": None},
                    ],
                },
                "organizations": [{"id": 9, "name": "Acme"}],
                "groups": [{"id": 8, "name": "Tier 1"}],
            }
        if endpoint == "tickets/7/comments.json":
            return {"comments": [
                {"id": 1, "author_id": 1, "public": True, "plain_body": "Help\nme <img src=x onerror=alert(1)>",
                 "created_at": "2026-01-01T00:00:00Z",
                 "via": {"channel": "email", "source": {
                     "from": {"address": "alice@example.com", "name": "Alice"},
                     "to": {"address": "support@x.zendesk.com", "email_ccs": ["cara@example.com"]}}},
                 "attachments": [{"file_name": "a.png", "content_url": "https://x/a.png"},
                                 {"file_name": "evil", "content_url": "javascript:alert(1)"}]},
                {"id": 2, "author_id": 2, "public": False, "plain_body": "internal",
                 "created_at": "2026-01-02T00:00:00Z", "attachments": []},
            ]}
        raise AssertionError(endpoint)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(operations, "_ticket_fields_cache", None)
    client = FakeClient()
    monkeypatch.setattr(operations, "_get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_custom_field_titles_and_labels(_fresh):
    ticket = {"custom_fields": [
        {"id": 100, "value": "widget_pro"}, {"id": 101, "value": ["mac", "win"]},
        {"id": 102, "value": "A-123"}, {"id": 999, "value": "x"},
    ]}
    await operations._annotate_custom_fields(_fresh, [ticket])
    cf = ticket["custom_fields"]
    assert (cf[0]["title"], cf[0]["value_label"]) == ("Product", "Widget Pro")
    assert cf[1]["value_label"] == ["macOS", "Windows"]
    assert cf[2]["title"] == "Order #" and "value_label" not in cf[2]
    assert "title" not in cf[3]  # unknown definition left alone


@pytest.mark.asyncio
async def test_field_definitions_cached(_fresh):
    for _ in range(2):
        await operations._annotate_custom_fields(_fresh, [{"custom_fields": [{"id": 100, "value": "x"}]}])
    assert _fresh.calls.count("ticket_fields.json") == 1


@pytest.mark.asyncio
async def test_export_markdown(tmp_path):
    out = tmp_path / "t.md"
    result = await operations.export_ticket("7", "md", str(out), include_content=True)
    md = out.read_text()
    assert result["comment_count"] == 2 and result["format"] == "markdown"
    assert "# Ticket #7: Broken <script>" in md
    assert "**Requester:** Alice <b> (1)" in md
    assert "**Organization:** Acme" in md and "**Group:** Tier 1" in md
    assert "**Requester:** Alice <b> (1) <alice@example.com>" in md
    assert "**CC:** Cara CC <cara@example.com>" in md
    assert "**Followers:** Bob <bob@brightstar.test>" in md
    assert "From: Alice <alice@example.com> · To: support@x.zendesk.com · CC: cara@example.com" in md
    assert "**Product:** Widget Pro" in md
    assert "**Affected OS:** macOS, Windows" in md
    assert "**Order #:** A-123" in md
    assert "Internal note" in md and "Public reply via email" in md
    assert "unknown def" in md  # unresolved field still shown, by id
    assert result["content"] == md


@pytest.mark.asyncio
async def test_export_html_is_escaped(tmp_path):
    out = tmp_path / "t.html"
    await operations.export_ticket("7", "html", str(out))
    doc = out.read_text()
    assert "<script>" not in doc and "<img" not in doc
    assert "&lt;script&gt;" in doc and "&lt;img src=x" in doc
    assert 'href="javascript:' not in doc
    assert '<a href="https://x/a.png">a.png</a>' in doc
    assert 'class="comment note"' in doc


def test_bad_format():
    with pytest.raises(ValueError):
        export.normalize_format("pdf")


@pytest.mark.asyncio
async def test_cc_and_follower_users_on_ticket(_fresh):
    ticket = {"requester_id": 1, "email_cc_ids": [3], "follower_ids": [2]}
    await operations._annotate_names(_fresh, tickets=[ticket])
    assert ticket["email_cc_users"] == [{"id": 3, "name": "Cara CC", "email": "cara@example.com"}]
    assert ticket["follower_users"][0]["email"] == "bob@brightstar.test"
    assert ticket["requester_email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_legacy_collaborators_fall_back_to_cc(_fresh):
    ticket = {"requester_id": 1, "collaborator_ids": [3]}
    await operations._annotate_names(_fresh, tickets=[ticket])
    assert [u["id"] for u in ticket["email_cc_users"]] == [3]
    assert "follower_users" not in ticket


@pytest.mark.asyncio
async def test_html_shows_recipients(tmp_path):
    out = tmp_path / "t.html"
    await operations.export_ticket("7", "html", str(out))
    doc = out.read_text()
    assert "CC: cara@example.com" in doc
    assert "Cara CC &lt;cara@example.com&gt;" in doc
