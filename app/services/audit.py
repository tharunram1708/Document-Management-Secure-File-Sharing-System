from app.models import AuditLog


def record_audit(db, action: str, *, actor_id: int | None = None, resource_type: str | None = None, resource_id: str | int | None = None, details: dict | None = None) -> None:
    db.add(AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        details=details,
    ))
