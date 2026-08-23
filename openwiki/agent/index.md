# Files

- [openwiki/agent/](AGENTS.md)
- [Coach graph routing and safe catalog output](coach-routing.md) - How the coach LangGraph selects safety, RAG relay, Route B tools, document, reminder, and erasure paths while constraining renderable model output.
- [Coach agent service](coach.md) - The LangGraph Agent Server "coach" graph and HTTP perimeter - routing, safety gate reuse, member data tools, reminders, uploads, feedback, and self-erase - deployed separately from the healthcare RAG graph.
- [Member data, documents, reminders, and erasure](member-data-lifecycle.md) - Namespaced scrubbed member records and the failure-aware lifecycles for uploads, review interrupts, reminder cron delivery, cleanup, and self-erasure.
- [Member authentication and API perimeter](member-perimeter.md) - The authentication principals, strict member route/envelope allowlist, ownership checks, and state projection that protect coach threads and actions.
