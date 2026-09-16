from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Blog, User
from app.services import blog_service
from app.services.blog_service import BlogError

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the bearer token to a user, or 401."""
    from app.services.auth_service import get_user_by_id, verify_access_token

    user_id = verify_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """The signed-in user, or None. Never 401s.

    For the routes an anonymous reader must reach but an author should see more
    of — a draft preview from its own author's browser, mostly.
    """
    from app.services.auth_service import get_user_by_id, verify_access_token

    if credentials is None:
        return None
    user_id = verify_access_token(credentials.credentials)
    if user_id is None:
        return None
    return get_user_by_id(db, user_id)


def own_blog(db: Session, user: User, blog_uuid: str) -> Blog:
    """A post that belongs to this user, or 404.

    404 rather than 403 on somebody else's post: whether a given uuid exists is
    not a question an unauthorised caller should get an answer to.
    """
    try:
        return blog_service.get_own_blog(db, user, blog_uuid)
    except BlogError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such post"
        )
