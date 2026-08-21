# Files

- [Runtime safety gate](gate.md) - First-touch classifier and PHI scrubber that refuses personal medical advice, emergencies, out-of-scope, and injection attempts with templated responses before the RAG pipeline runs.
- [Current medical and privacy safety posture](posture.md) - What the runtime safety gate and validation enforce, what remains unenforced, measured before/after impact, and required regression checks for this monograph RAG.
- [Presidio privacy sanitizer and direct-output policy](privacy-sanitizer.md) - Fail-closed PHI scanning with Presidio that backs scrub_phi everywhere, the deterministic clinical-code patterns, the input-size limit, and the policy that gates tool-arm direct answers.
