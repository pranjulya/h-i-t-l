# Concept 02 — Risk Is Not Policy

Risk estimates potential harm from an exact action and context. Policy decides whether the organization permits that risk and which obligations apply. Keeping them separate lets risk rules stay factual and policy vary by tenant/environment without hiding causality.

The V1 risk function starts from a tool minimum and only escalates. Production, greater blast radius, cost, irreversibility, sensitive data, or degraded context can raise the band. Unknown context fails closed rather than being interpreted as low risk.

Policy consumes the risk evidence plus current actor/resource context. It returns allow, require approval, or block; the approval route; TTL; required roles/scopes; and obligations. LOW is not guaranteed permission: maintenance or tenant policy can block it. MEDIUM is intentionally policy-driven. HIGH always needs one human. CRITICAL needs two distinct authorized humans.

Version every rule set and store factors/reason codes. A future policy update must not rewrite history. At execution the current policy is evaluated again; a stricter route stales approval rather than silently upgrading or downgrading it.

Self-check: classify staging scale 3→4, production scale 3→4, restart, high-cost provision, and delete. Then describe which facts came from risk and which decision came from policy.

