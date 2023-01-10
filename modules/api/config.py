from pydantic import BaseSettings, EmailStr

class Settings(BaseSettings):
    IMEZY_DB_PW: str
    IMEZY_DB_USER: str
    IMEZY_DB_SERVER: str
    IMEZY_DB_PORT: int
    
    JWT_ACCESS_KEY: str
    JWT_REFRESH_KEY: str
    GOOGLE_API_KEY: str
    JWT_ALGORITHM: str
    JWT_ACCESS_TOKEN_EXPIRE_HOURS: int
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int
    
    EMAIL_ADMIN_HOST: str
    EMAIL_ADMIN_PORT: int
    EMAIL_USERNAME: str
    EMAIL_ADMIN_PW: str
    EMAIL_ADMIN: EmailStr
    
    DEFAULT_CREDITS: int
    CREDITS_PER_IMAGE: int
    
    VERIFICATION_EXPIRE_SECONDS: int
    FEEDBACK_TYPE: dict
    
    
    class Config:
        env_file = "/data/StableDiffusion/stable-diffusion-webui-test/modules/api/conf/.env"
        env_file_encoding = "utf-8"
        
settings = Settings()