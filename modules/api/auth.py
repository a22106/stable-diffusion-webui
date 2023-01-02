from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from modules.api import models
from dataclasses import dataclass

from pydantic import ValidationError

# auth
from passlib.context import CryptContext
from jose import JWTError, jwt, ExpiredSignatureError

from . import exceptions
from .database import get_db
from sqlalchemy.orm import Session


SECRET_KEY_ACCESS = "secret_api_key"
SECRET_KEY_REFRESH = "secret_refresh"
ALGORITHM_ACCESS = "HS256"
ALGORITHM_REFRESH = "HS384"
ACCESS_TOKEN_EXPIRES_MINUTES = timedelta(minutes=120)
REFRESH_TOKEN_EXPIRES_MINUTES = timedelta(days=30)


oauth2_bearer = OAuth2PasswordBearer(tokenUrl="token")
bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def access_token_auth(token: str = Depends(oauth2_bearer)):
    try:
        payload = jwt.decode(token, SECRET_KEY_ACCESS, algorithms=[ALGORITHM_ACCESS])
        print(f"email: {payload.get('email')}, user_id: {payload.get('user_id')}, connected")
        t_type: str = payload.get("type")
        
        email: str = payload.get("email")
        user_id: int = payload.get("user_id")
        
        if email is None or user_id is None: # 키가 잘못된 경우
            print("get_current_user: email or user_id is None")
            raise exceptions.get_user_exception()
        
        return {"email": email, "user_id": user_id, "type": t_type} # 토큰이 유효한 경우
    
    except ExpiredSignatureError: # 토큰이 만료된 경우
        print("Access token is expired")
        raise exceptions.access_token_expired_exception()
    
    except JWTError: # 토큰이 유효하지 않은 경우
        print("Access token is invalid")
        raise exceptions.get_jwt_exception()
    
def refresh_token_auth(token: str = Depends(oauth2_bearer)):
    try:
        # if access token is expired, it will raise JWTError
        payload = jwt.decode(token, SECRET_KEY_REFRESH, algorithms=[ALGORITHM_REFRESH])
        token
        db = next(get_db())
        
        r_token_db = db.query(models.RefreshTokenDB).filter(models.RefreshTokenDB.email == payload.get('email')).first()
        
        if r_token_db.token != token:
            print("Refresh token is invalid")
            raise exceptions.refresh_token_expired_exception()
            
        print(f"email: {payload.get('email')}, user_id: {payload.get('user_id')}, connected")
        t_type: str = payload.get("type")
        
        email: str = payload.get("email")
        user_id: int = payload.get("user_id")
        
        if email is None or user_id is None:
            print("get_current_user: email or user_id is None")
            raise exceptions.get_user_exception()
        
        return {"email": email, "user_id": user_id, "type": t_type}
    except ExpiredSignatureError:
        print("Refresh token is expired. Please login again.")
        raise exceptions.refresh_token_expired_exception()
    except JWTError: # JWTError happens when token is expired or invalid
        print("Refresh token is invalid. Please login again.")
        
        raise exceptions.refresh_token_expired_exception()
    
def create_access_token(email: str, user_id: int, 
                    expires_delta: Optional[timedelta] = ACCESS_TOKEN_EXPIRES_MINUTES):
    to_encode = {"email": email, "user_id": user_id}
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=1)
    # update subject expire time and if it's access token
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY_ACCESS, algorithm=ALGORITHM_ACCESS)
    return encoded_jwt

# refresh token expires in 1 months
def create_refresh_token(email: str, user_id: int, 
                    expires_delta: Optional[timedelta] = REFRESH_TOKEN_EXPIRES_MINUTES):
    to_encode = {"email": email, "user_id": user_id}
    expire = datetime.utcnow() + expires_delta
    
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY_REFRESH, algorithm=ALGORITHM_REFRESH)
    
    return encoded_jwt

def not_authenticated_access_token(auth: dict, db: Session = None):
    '''
    description:
        - check if user is authenticated or not
        - raise exception if user is not authenticated
    args:
        - auth: dict = {email: str, user_id: int, type: str}
        - db: Session = database session
    return:
        - bool = True if user is authenticated, False if user is not authenticated
    '''
    
    if db:
        if (user_db := db.query(models.UsersDB).filter(models.UsersDB.email == auth['email']).first()) is None:
            print("User is not in database")
            raise exceptions.get_user_exception()
    
    if auth is None:
        print("User is not authenticated")
        raise exceptions.get_user_exception()
    if auth['type'] == 'refresh':
        return False
    return True

def get_password_hashed(password):
    return bcrypt_context.hash(password)

def verify_password(plain_password, hashed_password):
    return bcrypt_context.verify(plain_password, hashed_password)