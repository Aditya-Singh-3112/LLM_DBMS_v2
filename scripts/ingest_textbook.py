#!/usr/bin/env python3
"""
Ingestion script for RAG textbook data.

Usage:
  python scripts/ingest_textbook.py --source "Database System Concepts, 7ed" --file textbook.md

Uses LangChain's document loaders and text splitters.
"""

import argparse
import asyncio
from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import get_settings


async def ingest_textbook(
    source: str,
    file_path: str,
) -> None:
    """
    Ingest a textbook file into the vector store using LangChain.

    Args:
        source: Human-readable source name (e.g., "Database System Concepts, ch.4")
        file_path: Path to the markdown or text file
    """
    settings = get_settings()

    if not Path(file_path).exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    print(f"Loading file: {file_path}")
    loader = TextLoader(file_path, encoding="utf-8")
    docs = loader.load()

    print(f"File size: {len(docs[0].page_content)} characters")

    print("Chunking by markdown headers...")
    headers_to_split_on = [
        ("#", "Chapter"),
        ("##", "Section"),
        ("###", "Subsection"),
    ]

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        return_each_line=False,
        strip_headers=False,
    )

    split_docs = splitter.split_documents(docs)

    print(f"Created {len(split_docs)} chunks")

    for doc in split_docs:
        doc.metadata["source"] = source

    print("Initializing embeddings...")
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")

    print("Creating vector store and ingesting...")
    vector_store = PGVector(
        collection_name="textbook_chunks",
        connection_string=settings.postgres_uri,
        embedding_function=embeddings,
        use_jsonb=True,
    )

    ids = await vector_store.aadd_documents(split_docs)

    print(f"\n✓ Ingestion complete!")
    print(f"  Ingested {len(ids)} documents")
    print(f"  Source: {source}")


async def test_retrieval(source: str, query: str) -> None:
    """Quick test of retrieval quality."""
    settings = get_settings()

    print(f"\nTesting retrieval for query: '{query}'")

    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")

    vector_store = PGVector(
        collection_name="textbook_chunks",
        connection_string=settings.postgres_uri,
        embedding_function=embeddings,
        use_jsonb=True,
    )

    retriever = vector_store.as_retriever(search_kwargs={"k": 3})

    docs = await retriever.ainvoke(query)

    if not docs:
        print("  No results found!")
        return

    for i, doc in enumerate(docs, 1):
        print(f"\n  [{i}] {doc.metadata}")
        print(f"      Content: {doc.page_content[:200]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest a textbook into the RAG system"
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Source name (e.g., 'Database System Concepts, 7ed')",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the markdown or text file",
    )
    parser.add_argument(
        "--test-query",
        help="Optional: test retrieval with a sample query after ingestion",
    )

    args = parser.parse_args()

    asyncio.run(
        ingest_textbook(
            source=args.source,
            file_path=args.file,
        )
    )

    if args.test_query:
        asyncio.run(test_retrieval(args.source, args.test_query))