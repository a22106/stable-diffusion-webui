
from .database import engine, SessionLocal
from . import models
from .auth import verify_password, get_password_hash

from sqlalchemy.orm import Session

def get_db():
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()

def read_users(db):
    """Read all users from the database.

    Returns:
        A list of users.
    """
    return db.query(models.UsersDB).all()

def update_password(db: Session, user):
    """Update the password of a user in the database.

    Args:
        user: The user to update.
        password: The new password.

    Returns:
        The updated user.
    """
    user_updating = db.query(models.UsersDB).filter(models.UsersDB.email == user.email).first()

    if not verify_password(user.old_password, user_updating.hash_password):
        return False

    user_updating.hash_password = get_password_hash(user.new_password)
    db.add(user_updating)
    db.commit()
    db.refresh(user_updating)
    return user