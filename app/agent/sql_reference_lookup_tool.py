from pydantic import BaseModel, Field

from langchain_core.tools import StructuredTool

from app.services.rag_service import RAGService


class SqlReferenceLookupInput(BaseModel):
    query: str = Field(
        description="The SQL semantic question to look up in the textbook"
    )
    k: int = Field(
        default=4,
        ge=1,
        le=10,
        description="Number of passages to retrieve (default 4)",
    )


async def sql_reference_lookup_impl(rag_service: RAGService, query: str, k: int = 4) -> str:
    """Retrieve SQL/DBMS reference material from the textbook."""
    try:
        result = await rag_service.retrieve(query, k=k)

        if not result.passages:
            return "No relevant passages found in the textbook."

        passages_str = "\n---\n".join(
            [
                f"[{p.source}] {p.chapter or 'N/A'}\n{p.content}"
                for p in result.passages
            ]
        )

        return f"Retrieved {len(result.passages)} passages:\n{passages_str}"

    except Exception as e:
        return f"Error retrieving passages: {str(e)}"


def create_sql_reference_lookup_tool(rag_service: RAGService) -> StructuredTool:
    """Create the sql_reference_lookup tool for the agent."""
    return StructuredTool(
        name="sql_reference_lookup",
        description=(
            "Look up SQL and database concepts in the textbook. "
            "Use this before writing SQL with joins, subqueries, window functions, "
            "or when unsure about normalization or SQL semantics."
        ),
        func=lambda query, k=4: sql_reference_lookup_impl(
            rag_service, query, k
        ),
        args_schema=SqlReferenceLookupInput,
    )