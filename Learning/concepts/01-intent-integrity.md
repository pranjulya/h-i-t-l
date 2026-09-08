# Concept 01 — Exact Intent and Integrity

An action intent is the smallest complete description of an operation the system might authorize: tenant, requester, tool, revision, and strict typed parameters. Model text is only a proposal. Canonicalization validates the tool schema, normalizes values, rejects extras, serializes deterministically, and hashes a versioned envelope.

The digest answers “are these exact bytes of approved meaning unchanged?” It does not answer who approved, whether that person was authorized, or whether execution is safe now. Authenticated approval records and revalidation answer those questions.

Immutability removes ambiguous history. If replicas change from 3 to 30, editing the row would make old approval appear to cover new meaning. A new revision produces a new digest and runs risk/policy/approval again. Lifecycle fields may evolve; the approved action payload may not.

Database constraints protect aggregate identity and revision uniqueness. Pydantic protects input shape. Neither replaces the other: validation gives good errors and typed behavior; constraints preserve invariants across concurrent processes.

Self-check: two JSON objects with reordered keys but identical validated values must hash equally. Changing service, environment, count, resource ID, tenant, requester, tool, or revision must change the digest.

