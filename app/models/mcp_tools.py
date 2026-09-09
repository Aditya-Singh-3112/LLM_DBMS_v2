from typing import Any
from pydantic import BaseModel, Field

class ListSchemasRequest(BaseModel):
    database_id: str

class ListSchemasResponse(BaseModel):
    schemas: list[str]

class DescribeTableRequest(BaseModel):
    database_id: str
    schema_name: str
    table_name: str

class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: str

class DescribeTableResponse(BaseModel):
    table_name: str
    columns: list[ColumnInfo]

class SampleRowsRequest(BaseModel):
    database_id: str
    schema_name: str
    table_name: str
    limit: int = Field(default = 5, ge = 1, le = 100)

class SampleRowsResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]

class RunSqlRequest(BaseModel):
    database_id: str
    sql: str = Field(min_length = 1, max_length = 50_000)

class RunSqlResponse(BaseModel):
    columns: list[str] | None
    rows: list[list[Any]] | None

class ToolError(BaseModel):
    code: str
    message: str