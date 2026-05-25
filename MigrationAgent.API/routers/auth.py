"""
Auth Router — register, login, profile, user management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from database.db import get_db
from database.models import User
from middleware.auth import (
    hash_password, verify_password,
    create_token, get_current_user, require_admin
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response models ─────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    role: str = "user"   # default role is user

class LoginRequest(BaseModel):
    email: str
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    created_at: str

class UpdateRoleRequest(BaseModel):
    role: str


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user. First user is automatically admin."""
    # Check if email already exists
    existing = db.query(User).filter(User.email == request.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered."
        )

    # Validate role
    if request.role not in ("admin", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'."
        )

    # First user ever registered becomes admin automatically
    user_count = db.query(User).count()
    role = "admin" if user_count == 0 else request.role

    user = User(
        email=request.email,
        hashed_password=hash_password(request.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token(user.id, user.email, user.role)
    return {
        "message": "Registration successful.",
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }
    }


@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Login with email and password. Returns JWT token."""
    user = db.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    token = create_token(user.id, user.email, user.role)
    return {
        "message": "Login successful.",
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }
    }


@router.get("/me")
def get_profile(current_user: User = Depends(get_current_user)):
    """Get current user profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role,
        "created_at": str(current_user.created_at),
    }


@router.get("/users")
def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin only — list all users."""
    users = db.query(User).all()
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "created_at": str(u.created_at),
            }
            for u in users
        ],
        "count": len(users)
    }


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: str,
    request: UpdateRoleRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin only — change a user's role."""
    if request.role not in ("admin", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'."
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    user.role = request.role
    db.commit()
    return {"message": f"Role updated to '{request.role}'.", "user_id": user_id}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Admin only — delete a user."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account."
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(user)
    db.commit()
    return {"message": "User deleted successfully."}
