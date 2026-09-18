#!/usr/bin/env python3
"""
Ingestion script for RAG textbook data.

Usage (from the repo root):
  python -m scripts.ingest_textbook --source "Database System Concepts, 7ed" --file textbook.md

Uses the same embedding model, vector width and PGVector collection as
app.services.rag_service, so what is ingested here is what retrieval sees.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/ingest_textbook.py` as well as `python -m scripts.ingest_textbook`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_community.document_loaders import TextLoader  # noqa: E402
from langchain_text_splitters import MarkdownHeaderTextSplitter  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.rag_service import build_embeddings, build_vector_store  # noqa: E402


def _make_store():
    settings = get_settings()
    embeddings = build_embeddings(settings.google_api_key)
    return build_vector_store(settings.postgres_uri, embeddings)


async def ingest_textbook(source: str, file_path: str) -> None:
    """
    Ingest a markdown/text file into the vector store.

    Args:
        source: Human-readable source name (e.g., "Database System Concepts, ch.4")
        file_path: Path to the markdown or text file
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    print(f"Loading file: {file_path}")
    docs = TextLoader(file_path, encoding="utf-8").load()
    print(f"File size: {len(docs[0].page_content)} characters")

    print("Chunking by markdown headers...")
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "chapter"),
            ("##", "section"),
            ("###", "subsection"),
        ],
        return_each_line=False,
        strip_headers=False,
    )
    # MarkdownHeaderTextSplitter works on raw text and returns Documents
    # whose metadata holds the header values named above.
    split_docs = [
        chunk
        for doc in docs
        for chunk in splitter.split_text(doc.page_content)
    ]
    print(f"Created {len(split_docs)} chunks")

    for doc in split_docs:
        doc.metadata["source"] = source

    print("Creating vector store and ingesting...")
    vector_store = _make_store()
    ids = await vector_store.aadd_documents(split_docs)

    print("\n✓ Ingestion complete!")
    print(f"  Ingested {len(ids)} documents")
    print(f"  Source: {source}")


async def test_retrieval(query: str) -> None:
    """Quick test of retrieval quality."""
    print(f"\nTesting retrieval for query: '{query}'")

    vector_store = _make_store()
    results = await vector_store.asimilarity_search_with_score(query, k=3)

    if not results:
        print("  No results found!")
        return

    for i, (doc, score) in enumerate(results, 1):
        print(f"\n  [{i}] score={score:.4f} {doc.metadata}")
        print(f"      Content: {doc.page_content[:200]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest a textbook into the RAG system")
    parser.add_argument("--source", required=True, help="Source name (e.g., 'Database System Concepts, 7ed')")
    parser.add_argument("--file", required=True, help="Path to the markdown or text file")
    parser.add_argument("--test-query", help="Optional: test retrieval with a sample query after ingestion")

    args = parser.parse_args()

    asyncio.run(ingest_textbook(source=args.source, file_path=args.file))

    if args.test_query:
        asyncio.run(test_retrieval(args.test_query))
