# Files

- [openwiki/retrieval/](AGENTS.md) - Directory guide to the three A/B retrieval backends (Weaviate, PageIndex, Pinecone), the fail-soft cross-encoder reranker, and Weaviate's hybrid-search configuration, chunk schema, and ingestion/rebuild procedures.
- [Retrieval arms and reranking](arms-and-reranking.md) - A/B retrieval backends selectable with HC_RAG_RETRIEVER (weaviate, pageindex, pinecone) plus the fail-soft cross-encoder reranker, resolved per turn by the retrieve_documents node.
- [Weaviate retrieval and corpus ingestion](weaviate-and-ingestion.md) - Collection routing, hybrid search configuration, chunk schema, and safe destructive rebuild procedures.
