import hashlib
import json
from typing import Optional

from redis.asyncio import Redis
from langchain_postgres import PGVector
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.database import DatabaseManager
from app.models.rag import TextbookChunk, RetrievalResult

# gemini-embedding-001 defaults to 3072 dims; this must match the VECTOR(...)
# column width in rag.textbook_chunks (768 per the design doc). Passing
# output_dimensionality explicitly is required — some installed versions of
# langchain-google-genai silently ignore it if set only on the constructor.
EMBEDDING_DIMENSIONS = 768


class RAGService:
    """
    Retrieves relevant textbook passages via semantic similarity search.
    Uses LangChain's PGVector for vector storage and similarity search.
    Results are cached by normalized query.
    """

    CACHE_TTL_SECONDS = 86400  # 24 hours for reference material

    def __init__(self, database_manager: DatabaseManager, redis: Optional[Redis] = None) -> None:
        self.database_manager = database_manager
        self.redis = redis
        self._vector_store: Optional[PGVector] = None

    async def initialize(self) -> None:
        """Initialize the vector store (and sanity-check the embedding dimension)."""
        if self._vector_store is not None:
            return

        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        )

        # langchain_postgres.PGVector needs `connection` (not `connection_string`)
        # and `embeddings` (not `embedding_function`), and it requires psycopg3 —
        # the DSN must use the `postgresql+psycopg://` scheme, not plain
        # `postgresql://` or `+psycopg2`.
        connection = self._as_psycopg3_dsn(self.database_manager.settings.postgres_uri)

        self._vector_store = PGVector(
            collection_name="textbook_chunks",
            connection=connection,
            embeddings=embeddings,
            embedding_length=EMBEDDING_DIMENSIONS,
            use_jsonb=True,
        )

        # Fail loudly at startup, not on the first user query, if the model's
        # actual output dimension doesn't match what we told PGVector to expect.
        actual_dims = len(await embeddings.aembed_query("dimension check"))
        if actual_dims != EMBEDDING_DIMENSIONS:
            raise RuntimeError(
                f"Embedding model returned {actual_dims}-dim vectors but "
                f"RAGService/pgvector is configured for {EMBEDDING_DIMENSIONS}. "
                "output_dimensionality was likely ignored by this version of "
                "langchain-google-genai — either upgrade the package or widen "
                "the rag.textbook_chunks.embedding column to match."
            )

    async def retrieve(self, query: str, k: int = 4, max_k: int = 10) -> RetrievalResult:
        """
        Retrieve the k most similar textbook chunks to the query.
        Results are cached for 24 hours.
        """
        if self._vector_store is None:
            await self.initialize()

        if k > max_k:
            k = max_k

        cache_key = self._cache_key(query, k)

        if self.redis is not None:
            cached = await self.redis.get(cache_key)
            if cached is not None:
                data = json.loads(cached)
                return RetrievalResult(
                    passages=[TextbookChunk(**p) for p in data["passages"]],
                    from_cache=True,
                )

        try:
            # Query the store directly instead of going through .as_retriever():
            # the retriever bakes `k` in once at construction time (so a
            # per-call k is silently ignored) and strips the score out of
            # doc.metadata entirely. similarity_search_with_score gives both.
            results = await self._vector_store.asimilarity_search_with_score(query, k=k)

            chunks = [
                TextbookChunk(
                    source=doc.metadata.get("source", ""),
                    chapter=doc.metadata.get("chapter"),
                    page_start=doc.metadata.get("page_start"),
                    page_end=doc.metadata.get("page_end"),
                    content=doc.page_content,
                    similarity_score=score,
                )
                for doc, score in results
            ]

        except Exception as e:
            raise RuntimeError(f"Failed to retrieve documents: {str(e)}") from e

        result = RetrievalResult(passages=chunks, from_cache=False)

        if self.redis is not None:
            await self.redis.setex(
                cache_key,
                self.CACHE_TTL_SECONDS,
                json.dumps({"passages": [c.model_dump() for c in chunks]}),
            )

        return result

    @staticmethod
    def _as_psycopg3_dsn(dsn: str) -> str:
        """Normalize a plain/psycopg2 Postgres DSN to the psycopg3 scheme PGVector needs."""
        if dsn.startswith("postgresql+psycopg://"):
            return dsn
        if dsn.startswith("postgresql+psycopg2://"):
            return dsn.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
        return dsn

    @staticmethod
    def _cache_key(query: str, k: int) -> str:
        normalized = query.lower().strip()
        query_hash = hashlib.md5(normalized.encode()).hexdigest()
        return f"rag:retrieve:{query_hash}:{k}"