from datetime import datetime, timezone

from app.models.contracts import AskResponse, QueryResult
from app.models.mcp_tools import ColumnInfo, DescribeTableRequest, SampleRowsRequest
from app.models.tool_call_log import ToolCallEntry, ToolCallSource, ToolCallStatus

import pytest
from pydantic import ValidationError


def test_column_info_accepts_bool_nullable():
    assert ColumnInfo(name="a", type="int", nullable=True).nullable is True


def test_ask_response_accepts_tool_call_entries():
    entry = ToolCallEntry(
        tool_name="run_sql",
        args={"sql": "SELECT 1"},
        result="ok",
        status=ToolCallStatus.SUCCESS,
        duration_ms=0,
        timestamp=datetime.now(timezone.utc),
        source=ToolCallSource.MCP,
    )
    response = AskResponse(
        answer="a",
        tool_calls=[entry],
        result=QueryResult(columns=[], rows=[]),
        conversation_id="c1",
        total_execution_time_ms=1,
    )
    assert response.tool_calls[0].tool_name == "run_sql"


@pytest.mark.parametrize("bad", ['users"; --', "users; drop", "1abc", "a" * 64, "sch.tbl"])
def test_table_name_must_be_plain_identifier(bad):
    with pytest.raises(ValidationError):
        DescribeTableRequest(database_id="x", table_name=bad)
    with pytest.raises(ValidationError):
        SampleRowsRequest(database_id="x", table_name=bad)


def test_requests_no_longer_accept_caller_supplied_schema():
    req = DescribeTableRequest(database_id="x", table_name="users", schema_name="tenant_other")
    assert not hasattr(req, "schema_name")
