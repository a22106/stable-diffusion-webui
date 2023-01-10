
from .database import engine, SessionLocal, get_db
from . import models
from .auth import verify_password, get_password_hashed

from sqlalchemy.orm import Session


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

    if not verify_password(user.old_password, user_updating.hashed_password):
        return False

    user_updating.hashed_password = get_password_hashed(user.new_password)
    db.add(user_updating)
    db.commit()
    db.refresh(user_updating)
    return True

def update_user(db: Session, user_id, user):
    """Update the password of a user in the database.

    Args:
        user: The user to update. Contains name, email, is_active, and is_admin.
    Returns:
        The updated user.
    """
    user_updating = db.query(models.UsersDB).filter(models.UsersDB.id == user_id).first()

    
    
    for attr in user.__dict__.keys():
        if getattr(user, attr) != None:
            print(f"{attr}: {getattr(user, attr)}")
            setattr(user_updating, attr, getattr(user, attr))
        
    db.add(user_updating)
    db.commit()
    db.refresh(user_updating)
    
    response_user_info = user_updating.__dict__
    del response_user_info["hashed_password"], response_user_info["created_date"]
    response = {"code": 200, "message": "User updated", "data": response_user_info}
    return response

def delete_user(db: Session, user_id):
    """Delete a user from the database.

    Args:
        user_id: The id of the user to delete.

    Returns:
        The deleted user.
    """
    user = db.query(models.UsersDB).filter(models.UsersDB.id == user_id).first()
    db.delete(user)
    db.commit()
    return user