"""Tests for requester/assignee/author name resolution."""

import pytest

from zendesk_skill import operations
from zendesk_skill.client import ZendeskAPIError


class FakeClient:
    def __init__(self, users=None, fail=False):
        self.users = users or {}
        self.fail = fail
        self.calls = []

    async def get(self, endpoint, params=None, timeout=None):
        self.calls.append((endpoint, params))
        if self.fail:
            raise ZendeskAPIError("boom", 403)
        ids = [int(i) for i in params["ids"].split(",")]
        return {"users": [{"id": i, "name": self.users[i]} for i in ids if i in self.users]}


@pytest.mark.asyncio
async def test_annotates_tickets_and_comments():
    client = FakeClient({1: "Alice", 2: "Bob", 3: "Cara"})
    tickets = [
        {"id": 10, "requester_id": 1, "assignee_id": 2},
        {"id": 11, "requester_id": 1, "assignee_id": None},
    ]
    comments = [{"id": 5, "author_id": 3}]

    await operations._annotate_names(client, tickets=tickets, comments=comments)

    assert tickets[0]["requester_name"] == "Alice"
    assert tickets[0]["assignee_name"] == "Bob"
    assert tickets[1]["requester_name"] == "Alice"
    assert "assignee_name" not in tickets[1]
    assert comments[0]["author_name"] == "Cara"
    assert len(client.calls) == 1  # one batched call, IDs deduplicated


@pytest.mark.asyncio
async def test_batches_in_groups_of_100():
    client = FakeClient({i: f"U{i}" for i in range(1, 251)})
    tickets = [{"id": i, "requester_id": i} for i in range(1, 251)]

    await operations._annotate_names(client, tickets=tickets)

    assert len(client.calls) == 3
    assert all(len(p["ids"].split(",")) <= 100 for _, p in client.calls)
    assert tickets[249]["requester_name"] == "U250"


@pytest.mark.asyncio
async def test_lookup_failure_is_not_fatal():
    client = FakeClient(fail=True)
    tickets = [{"id": 10, "requester_id": 1}]

    await operations._annotate_names(client, tickets=tickets)

    assert "requester_name" not in tickets[0]


@pytest.mark.asyncio
async def test_unresolved_users_left_unannotated():
    client = FakeClient({1: "Alice"})
    tickets = [{"id": 10, "requester_id": 1, "assignee_id": 99}]

    await operations._annotate_names(client, tickets=tickets)

    assert tickets[0]["requester_name"] == "Alice"
    assert "assignee_name" not in tickets[0]
