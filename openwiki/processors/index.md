# Files

- [openwiki/processors/](AGENTS.md) - Directory guide to each LangGraph stage's processor: which prompt template and Pydantic response model it uses, which model tier it runs on, and the owning node, plus the answer-structuring and fuzzy citation-verification pipeline.
- [Graph stages, prompts, and models](overview.md) - Mapping of each LangGraph stage to its prompt template, Pydantic output model, model tier, and owning node, plus the extension rules for adding or changing a stage.
- [Answer structuring and citation verification](validation.md) - How raw answers become structured cited statements, how leaked prompt scaffolding and unsupported citations are removed, and the exact fallback outcomes.
