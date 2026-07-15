from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Domain models (Signal, RiskDecision, Order,
    Position, AuditEvent, SystemState — PROJECT.md Section 7) are added in
    Phase 2 alongside their first Alembic migration; this phase only wires
    up the database connection and migration tooling."""
