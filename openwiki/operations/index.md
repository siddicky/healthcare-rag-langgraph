# Files

- [Production deploy (Fly, tag pipeline)](deploy.md) - Prod-only Fly deploy of the server image - compliance gate, GitHub Environment production as the single secrets source, immutable-digest tag pipeline in .github/workflows/deploy.yml, Weaviate companion app, prod ingest, and the synthetic-account deployed smoke. Summarizes docs/deploy.md.
- [Local development and Weaviate operations](runbook.md) - Set up with uv, operate the local vector store, ingest corpus chunks, run the CLI, and recover safely.
