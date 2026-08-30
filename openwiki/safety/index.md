# Files

- [openwiki/safety/](AGENTS.md) - Directory guide to the runtime safety gate (first-touch classifier plus PHI scrubber) and the overall medical/privacy safety posture: what's enforced, what isn't, measured before/after impact, and required regression checks.
- [Runtime safety gate](gate.md) - First-touch classifier and PHI scrubber that refuses personal medical advice, emergencies, out-of-scope, and injection attempts with templated responses before the RAG pipeline runs.
- [Healthcare and privacy safety posture](posture.md) - What this monograph RAG answers, refuses, or redirects; what personal/sensitive data must never be collected, retained, logged, or sent to model providers; measured before/after safety metrics; and explicit known gaps.
