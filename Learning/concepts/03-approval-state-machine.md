# Concept 03 — Approval as a State Machine

A state machine makes lifecycle authority finite and testable. Clients submit commands; they never assign states. The transition service loads the current revision, verifies expected state/version, checks command-specific guards, appends evidence, increments the version, and emits an audit outbox record in one transaction.

Approval evidence binds a human decision to one digest, revision, policy route, level, identity, scope, and expiry. Authentication establishes who the caller is. Authorization establishes what that identity may approve now. A stored role snapshot explains the historic decision but cannot substitute for current authorization during execution.

Separation of duties is a domain invariant: HIGH requesters cannot self-approve; CRITICAL requester, L1, and L2 are three distinct principals. L2 begins only after committed L1 approval, preventing parallel decisions from accidentally satisfying ordered review.

Races are normal. If rejection and execution claim compete, row locking/state-version comparison gives one committed winner. Before claim, rejection stops execution. After claim, cancellation/rejection cannot pretend the provider call never began.

Self-check: name every state from request through critical approval and success, then identify where expiry, rejection, cancellation, stale context, and ambiguous execution land.

