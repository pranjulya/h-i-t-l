# Scenario 01 — LOW Service Inspection

An operator asks, “Why is staging checkout unhealthy?” The model proposes `inspect_service` for the authenticated tenant, `staging`, and `checkout`.

Expected reasoning:

1. Strict validation rejects any model-added endpoint, command, credential, or unknown field.
2. Canonical intent and digest are stored as `REQUESTED`.
3. Risk is LOW; policy auto-allows because actor scope and read policy pass.
4. Worker claims `AUTO_APPROVED`, revalidates live target identity and current policy, then runs a typed read.
5. Safe transient reads may retry within bounds. Sanitized result and full audit evidence persist.

Failure variation: if the service name is outside actor scope, policy/authorization blocks it despite LOW risk. LOW means limited harm, not universal permission.

