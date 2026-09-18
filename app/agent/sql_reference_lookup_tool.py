from pydantic import BaseModel, Field

from langchain_core.tools import StructuredTool

from app.models.rag import TextbookChunk
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


class SqlReferenceLookupTool:
    """
    Wraps RAGService as a LangChain tool and records every passage it
    returned so the API layer can report real grounding metadata.
    """

    def __init__(self, rag_service: RAGService) -> None:
        self.rag_service = rag_service
        self.retrieved_passages: list[TextbookChunk] = []

    async def lookup(self, query: str, k: int = 4) -> str:
        """Retrieve SQL/DBMS reference material from the textbook."""
        try:
            result = await self.rag_service.retrieve(query, k=k)
        except Exception as e:
            return f"Error retrieving passages: {str(e)}"

        if not result.passages:
            return "No relevant passages found in the textbook."

        self.retrieved_passages.extend(result.passages)

        passages_str = "\n---\n".join(
            [
                f"[{p.source}] {p.chapter or 'N/A'}\n{p.content}"
                for p in result.passages
            ]
        )

        return f"Retrieved {len(result.passages)} passages:\n{passages_str}"

    def as_structured_tool(self) -> StructuredTool:
        return StructuredTool(
            name="sql_reference_lookup",
            description=(
                "Look up SQL and database concepts in the textbook. "
                "Use this before writing SQL with joins, subqueries, window functions, "
                "or when unsure about normalization or SQL semantics."
            ),
            coroutine=self.lookup,
            args_schema=SqlReferenceLookupInput,
        )


def create_sql_reference_lookup_tool(rag_service: RAGService) -> SqlReferenceLookupTool:
    """Create the sql_reference_lookup tool for the agent."""
    return SqlReferenceLookupTool(rag_service)
