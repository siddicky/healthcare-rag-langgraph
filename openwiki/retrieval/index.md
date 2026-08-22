# Files

- [Retrieval arms and reranking](arms-and-reranking.md) - The three interchangeable retrieval backends selectable with HC_RAG_RETRIEVER (weaviate hybrid, pageindex tree-search, pinecone serverless hybrid), the fail-soft Pinecone reranker, their ingestion/index commands, and how arms are selected and gated per turn by the retrieve_documents node.
- [Weaviate retrieval and corpus ingestion](weaviate-and-ingestion.md) - Collection routing, hybrid search configuration, chunk schema, and safe destructive rebuild procedures.
