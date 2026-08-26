# Files

- [openwiki/safety/](AGENTS.md) - Directory guide to the runtime safety gate (first-touch classifier plus PHI scrubber) and the overall medical/privacy safety posture: what's enforced, what isn't, measured before/after impact, and required regression checks.
- [Runtime safety gate](gate.md) - First-touch classifier and PHI scrubber that refuses personal medical advice, emergencies, out-of-scope, and injection attempts with templated responses before the RAG pipeline runs.
- [Current medical and privacy safety posture](posture.md) - What the runtime safety gate and validation enforce, what remains unenforced, measured before/after impact, and required regression checks for this monograph RAG.
