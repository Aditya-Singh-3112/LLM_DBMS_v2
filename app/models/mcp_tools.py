from typing import Any
from pydantic import BaseModel, Field

# Unquoted Postgres identifier: letters/underscore start, max 63 bytes.
IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"


class ListSchemasRequest(BaseModel):
    database_id: str

class ListSchemasResponse(BaseModel):
    schemas: list[str]

class DescribeTableRequest(BaseModel):
    database_id: str
    table_name: str = Field(pattern=IDENTIFIER_PATTERN)

class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool

class DescribeTableResponse(BaseModel):
    table_name: str
    columns: list[ColumnInfo]

class SampleRowsRequest(BaseModel):
    database_id: str
    table_name: str = Field(pattern=IDENTIFIER_PATTERN)
    limit: int = Field(default = 5, ge = 1, le = 100)

class SampleRowsResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]

class RunSqlRequest(BaseModel):
    database_id: str
    sql: str = Field(min_length = 1, max_length = 50_000)
    # Writes are only executed when the user has explicitly confirmed them.
    confirmed: bool = False

class RunSqlResponse(BaseModel):
    columns: list[str] | None
    rows: list[list[Any]] | None

class ToolError(BaseModel):
    code: str
    message: str
