"""
Accounts, sessions and the way back in when a password is forgotten.

Password storage is PBKDF2-SHA256 with a per-password salt, in the
self-describing `iterations$salt$hash` format, so the cost can be raised later
without invalidating anybody's existing hash.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import hashlib
import logging
import os
import secrets

import jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import PasswordResetCode, RefreshToken, User

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    # Never fall back to a constant: if the secret failed to reach the
    # container the app would keep serving and sign tokens with a key anyone
    # could read out of this repo. Refusing to start makes that visible.
    raise RuntimeError(
        "SECRET_KEY is not set. Generate one with `openssl rand -hex 32` and "
        "supply it via the environment (in k8s it comes from the humlog-env "
        "ExternalSecret)."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Password hashing. No rules on what a password may be, only on how it's stored.
SALT_LENGTH = 32  # bytes
HASH_ITERATIONS = 100000  # OWASP recommended minimum

# Reset codes. Letters only, and only the ones nobody misreads: no I next to l,
# no O next to 0, because this is a code somebody copies off their phone by
# hand. 8 letters out of 24 is ~37 bits, which five attempts and fifteen
# minutes make unguessable.
RESET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
RESET_CODE_LENGTH = 8
RESET_CODE_TTL_MINUTES = 15
RESET_CODE_MAX_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def get_password_hash(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and a fresh random salt."""
    salt = secrets.token_bytes(SALT_LENGTH)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, HASH_ITERATIONS
    ).hex()
    # Format: iterations$salt$hash — self-describing, so the cost can be
    # raised later without invalidating existing hashes.
    return f"{HASH_ITERATIONS}${salt.hex()}${password_hash}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a stored hash."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False

        iterations = int(parts[0])
        salt = bytes.fromhex(parts[1])
        stored_hash = parts[2]

        computed_hash = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, iterations
        ).hex()

        # Constant-time comparison to prevent timing attacks
        return secrets.compare_digest(computed_hash, stored_hash)
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: int, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "type": "access"},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(db: Session, user_id: int) -> str:
    """Create a refresh token, store it, and sweep this user's expired ones."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    # jti keeps the encoded token unique. Without it the payload is a pure
    # function of (user_id, exp), exp only has second resolution, and two
    # logins in the same second would collide on the unique token index.
    token = jwt.encode(
        {
            "sub": str(user_id),
            "exp": expires_at,
            "type": "refresh",
            "jti": secrets.token_urlsafe(16),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    try:
        db.query(RefreshToken).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.expires_at <= now,
        ).delete(synchronize_session=False)
    except Exception as e:
        logger.warning(f"Failed to clean expired tokens for user {user_id}: {e}")

    db.add(RefreshToken(user_id=user_id, token=token, expires_at=expires_at))
    db.commit()
    return token


def verify_access_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return int(payload.get("sub"))
    except jwt.InvalidTokenError:
        return None


def verify_refresh_token(db: Session, token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        user_id = int(payload.get("sub"))
    except jwt.InvalidTokenError:
        return None

    # A signed-but-revoked token must not work, so the row has to exist too.
    db_token = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == token,
            RefreshToken.user_id == user_id,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )
    return user_id if db_token else None


def revoke_refresh_token(db: Session, token: str) -> bool:
    db_token = db.query(RefreshToken).filter(RefreshToken.token == token).first()
    if not db_token:
        return False
    db.delete(db_token)
    db.commit()
    return True


def revoke_all_user_tokens(db: Session, user_id: int) -> None:
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete()
    db.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Case-insensitively, because nobody remembers how they capitalised it."""
    return db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()


def find_user_for_login(db: Session, identifier: str) -> Optional[User]:
    """Resolve whatever was typed into the "username or email" box."""
    identifier = identifier.strip()
    if "@" in identifier:
        return get_user_by_email(db, identifier)
    return get_user_by_username(db, identifier.lower())


def create_user(
    db: Session, username: str, email: str, password: str, display_name: str
) -> User:
    user = User(
        username=username,
        email=email,
        display_name=display_name or username,
        password_hash=get_password_hash(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, identifier: str, password: str) -> Optional[User]:
    user = find_user_for_login(db, identifier)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def change_password(db: Session, user: User, new_password: str) -> None:
    """Set a new password and log every session out."""
    user.password_hash = get_password_hash(new_password)
    db.commit()
    revoke_all_user_tokens(db, user.id)


# ---------------------------------------------------------------------------
# Forgotten passwords
# ---------------------------------------------------------------------------

def _hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def issue_reset_code(db: Session, user: User) -> str:
    """Mint a fresh reset code for a user and return the plaintext, once.

    Only the hash is stored, so this return value is the only time anybody —
    including this server — can read the code. Asking again invalidates the
    previous one: two live codes would double the guessing surface for no gain.
    """
    db.query(PasswordResetCode).filter(
        PasswordResetCode.user_id == user.id
    ).delete(synchronize_session=False)

    code = "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(RESET_CODE_LENGTH))
    db.add(
        PasswordResetCode(
            user_id=user.id,
            code_hash=_hash_reset_code(code),
            expires_at=datetime.now(timezone.utc)
            + timedelta(minutes=RESET_CODE_TTL_MINUTES),
        )
    )
    db.commit()
    return code


def redeem_reset_code(db: Session, user: User, code: str) -> bool:
    """Spend a reset code. True only if it was this user's, live, and correct.

    A wrong answer costs an attempt, and the fifth wrong answer burns the code
    outright — otherwise the fifteen-minute window is fifteen minutes of free
    guessing.
    """
    now = datetime.now(timezone.utc)

    # Expired codes are gone the moment anybody looks, so a stale row can never
    # be the one that gets checked.
    db.query(PasswordResetCode).filter(
        PasswordResetCode.user_id == user.id,
        PasswordResetCode.expires_at <= now,
    ).delete(synchronize_session=False)
    db.commit()

    record = (
        db.query(PasswordResetCode)
        .filter(PasswordResetCode.user_id == user.id)
        .order_by(PasswordResetCode.id.desc())
        .first()
    )
    if record is None:
        return False

    if secrets.compare_digest(record.code_hash, _hash_reset_code(code)):
        db.delete(record)
        db.commit()
        return True

    record.attempts += 1
    if record.attempts >= RESET_CODE_MAX_ATTEMPTS:
        logger.warning(f"Reset code for user {user.id} burned after too many attempts")
        db.delete(record)
    db.commit()
    return False
