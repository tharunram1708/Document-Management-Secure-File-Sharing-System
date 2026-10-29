from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models import User
from app.core.roles import Role
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserCreate, UserRead, UserRoleUpdate
from app.services.audit import record_audit
from app.services.cache import invalidate_documents
from app.services.rate_limit import login_limit_exceeded

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, full_name=payload.full_name.strip(), hashed_password=hash_password(payload.password))
    db.add(user)
    try:
        db.flush()
        record_audit(db, "auth.register", actor_id=user.id, resource_type="user", resource_id=user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email = str(payload.email).lower()
    identity = request.client.host if request.client else "unknown"
    if login_limit_exceeded(identity):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later", headers={"Retry-After": str(settings.login_rate_window_seconds)})
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.hashed_password) or not user.is_active:
        record_audit(db, "auth.login.failure", details={"email": email})
        db.commit()
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    record_audit(db, "auth.login.success", actor_id=user.id, resource_type="user", resource_id=user.id)
    db.commit()
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/users/{user_id}/role", response_model=UserRead)
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_roles(Role.ADMIN)),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.role = payload.role.value
    record_audit(db, "auth.user.role_update", actor_id=_admin.id, resource_type="user", resource_id=user.id, details={"role": payload.role.value})
    db.commit()
    invalidate_documents()
    db.refresh(user)
    return user
