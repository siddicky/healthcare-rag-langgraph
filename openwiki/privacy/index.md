# Files

- [openwiki/privacy/](AGENTS.md) - Directory guide to the Presidio-backed privacy sanitizer: a process-wide, fail-closed PII scanner that backs scrub_phi, used by the RAG graph, the CLI monitor, and the coach agent's tools and store writes.
- [Privacy sanitizer and PHI/PII scrubbing](sanitizer.md) - PrivacySanitizer and the deterministic scrub layer that redacts PHI/PII before text reaches model providers, logs, traces, or persisted state, plus the direct-output policy that gates tool-arm answers on the same scan.
