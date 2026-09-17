"""Redis-loss tests: correctness never depends on Redis.

The V1 architecture is PostgreSQL-only for correctness (ADR-006). These tests
document and pin that property: no Redis client exists in the dependency tree,
and the full claim/execute flow runs with no Redis configuration at all.
"""

from __future__ import annotations


def test_no_redis_dependency_is_importable_or_required() -> None:
    import importlib.util

    assert importlib.util.find_spec("redis") is None
    assert importlib.util.find_spec("aioredis") is None


def test_settings_carry_no_required_redis_configuration() -> None:
    from hitl_ops.config import Settings

    resolved = Settings(_env_file=None)
    fields = resolved.model_fields
    assert not any("redis" in name.lower() for name in fields)


async def test_claim_flow_runs_without_any_cache_layer(failure_database, engine) -> None:
    from hitl_ops.application.revalidation import ExecutionPermit, RevalidationService
    from tests.integration.conftest import create_approved_intent

    maker = build_sessionmaker_local(engine)
    async with maker() as session, session.begin():
        pending = await create_approved_intent(
            session,
            tool="scale_service",
            parameters={"environment": "staging", "service": "api", "replicas": 2},
        )
        service = RevalidationService(session)

        class Target:
            async def fetch(self, tool: object, parameters: dict):
                from hitl_ops.adapters.base import TargetSnapshot

                return TargetSnapshot(
                    found=True,
                    identity={"service": parameters.get("service")},
                    health="healthy",
                )

        permit = await service.claim_and_revalidate(
            tenant_id="tenant-1",
            intent_id=pending["intent_id"],
            revision=1,
            worker_id="worker-no-redis",
            command_id="cmd",
            target_query=Target(),
            bundle=_bundle(),
        )
    assert isinstance(permit, ExecutionPermit)


def _bundle():
    from hitl_ops.domain.policy import SEED_POLICY_BUNDLE_RULES, PolicyBundle

    return PolicyBundle(version="policy-1", rules=SEED_POLICY_BUNDLE_RULES)


def build_sessionmaker_local(engine):
    from hitl_ops.infrastructure.database import build_sessionmaker

    return build_sessionmaker(engine)
