''' 할 일

- [ ] 1. 기능별로 라우터를 나누어서 api.py에 합치기
- [v] 2. img2img 등 기능에도 크래딧 차감 기능 추가하기
- [ ] 3. 크래딧 업데이트에 적용한 refesh token으로 접근 시 access token을 새로 발급해주는 기능 추가하기
- [ ] 4. 생성된 사진 용량 줄여서 저장하기

'''

import base64
import io, os
import time
import uvicorn, json
from dotenv import load_dotenv
from threading import Lock
from io import BytesIO
from gradio.processing_utils import decode_base64_to_file
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from secrets import compare_digest
from datetime import datetime, timedelta
from fastapi_jwt_auth import AuthJWT
from fastapi_jwt_auth.exceptions import AuthJWTException

import modules.shared as shared
from modules import sd_samplers, deepbooru, sd_hijack
from modules.api.models import *
from modules.processing import StableDiffusionProcessingTxt2Img, StableDiffusionProcessingImg2Img, process_images
from modules.extras import run_extras, run_pnginfo
from modules.textual_inversion.textual_inversion import create_embedding, train_embedding
from modules.textual_inversion.preprocess import preprocess
from modules.hypernetworks.hypernetwork import create_hypernetwork, train_hypernetwork
from PIL import PngImagePlugin,Image
from modules.sd_models import checkpoints_list
from modules.realesrgan_model import get_realesrgan_models
from modules import devices
from typing import List

from passlib.context import CryptContext
from .database import engine, get_db
from sqlalchemy.orm import Session
import sqlalchemy.exc as exc
from sqlalchemy.orm.exc import FlushError
from . import models, credits

from .users import *
from .auth import *
from .logs import print_message

models.Base.metadata.create_all(bind=engine)

DEFAULT_CREDITS = 500
CREDITS_PER_IMAGE = 10
with open('modules/api/configs.json', 'r') as f:
    IMEZY_CONFIG = json.load(f)

def upscaler_to_index(name: str):
    try:
        return [x.name.lower() for x in shared.sd_upscalers].index(name.lower())
    except:
        raise HTTPException(status_code=400, detail=f"Invalid upscaler, needs to be on of these: {' , '.join([x.name for x in sd_upscalers])}")

def validate_sampler_name(name):
    config = sd_samplers.all_samplers_map.get(name, None)
    if config is None:
        raise HTTPException(status_code=404, detail="Sampler not found")

    return name

def setUpscalers(req: dict):
    reqDict = vars(req)
    reqDict['extras_upscaler_1'] = upscaler_to_index(req.upscaler_1)
    reqDict['extras_upscaler_2'] = upscaler_to_index(req.upscaler_2)
    reqDict.pop('upscaler_1')
    reqDict.pop('upscaler_2')
    return reqDict

def decode_base64_to_image(encoding):
    if encoding.startswith("data:image/"):
        encoding = encoding.split(";")[1].split(",")[1]
    return Image.open(BytesIO(base64.b64decode(encoding)))

def encode_pil_to_base64(image):
    with io.BytesIO() as output_bytes:

        # Copy any text-only metadata
        use_metadata = False
        metadata = PngImagePlugin.PngInfo()
        for key, value in image.info.items():
            if isinstance(key, str) and isinstance(value, str):
                metadata.add_text(key, value)
                use_metadata = True

        image.save(
            output_bytes, "PNG", pnginfo=(metadata if use_metadata else None)
        )
        bytes_data = output_bytes.getvalue()
    return base64.b64encode(bytes_data)
    
class Api:
    def __init__(self, app: FastAPI, queue_lock: Lock):
        if shared.cmd_opts.api_auth:
            self.credentials = dict()
            for auth in shared.cmd_opts.api_auth.split(","):
                user, password = auth.split(":")
                self.credentials[user] = password

        self.router = APIRouter()
        self.app = app
        self.queue_lock = queue_lock
        self.add_api_route("/sdapi/v1/txt2img", self.text2imgapi, methods=["POST"], response_model=TextToImageResponse)
        self.add_api_route("/sdapi/v1/txt2img-auth", self.text2imgapi_auth, methods=["POST"], response_model=TextToImageResponse)
        self.add_api_route("/sdapi/v1/img2img", self.img2imgapi, methods=["POST"], response_model=ImageToImageResponse)
        self.add_api_route("/sdapi/v1/img2img-auth", self.img2imgapi_auth, methods=["POST"], response_model=ImageToImageResponse)
        self.add_api_route("/sdapi/v1/extra-single-image", self.extras_single_image_api, methods=["POST"], response_model=ExtrasSingleImageResponse)
        self.add_api_route("/sdapi/v1/extra-single-image-auth", self.extras_single_image_api_auth, methods=["POST"], response_model=ExtrasSingleImageResponse)
        self.add_api_route("/sdapi/v1/extra-batch-images", self.extras_batch_images_api, methods=["POST"], response_model=ExtrasBatchImagesResponse)
        self.add_api_route("/sdapi/v1/png-info", self.pnginfoapi, methods=["POST"], response_model=PNGInfoResponse)
        self.add_api_route("/sdapi/v1/progress", self.progressapi, methods=["GET"], response_model=ProgressResponse)
        self.add_api_route("/sdapi/v1/interrogate", self.interrogateapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/interrupt", self.interruptapi, methods=["POST"])
        self.add_api_route("/sdapi/v1/skip", self.skip, methods=["POST"])
        self.add_api_route("/sdapi/v1/options", self.get_config, methods=["GET"], response_model=OptionsModel)
        self.add_api_route("/sdapi/v1/options", self.set_config, methods=["POST"])
        self.add_api_route("/sdapi/v1/cmd-flags", self.get_cmd_flags, methods=["GET"], response_model=FlagsModel)
        self.add_api_route("/sdapi/v1/samplers", self.get_samplers, methods=["GET"], response_model=List[SamplerItem])
        self.add_api_route("/sdapi/v1/upscalers", self.get_upscalers, methods=["GET"], response_model=List[UpscalerItem])
        self.add_api_route("/sdapi/v1/sd-models", self.get_sd_models, methods=["GET"], response_model=List[SDModelItem])
        self.add_api_route("/sdapi/v1/hypernetworks", self.get_hypernetworks, methods=["GET"], response_model=List[HypernetworkItem])
        self.add_api_route("/sdapi/v1/face-restorers", self.get_face_restorers, methods=["GET"], response_model=List[FaceRestorerItem])
        self.add_api_route("/sdapi/v1/realesrgan-models", self.get_realesrgan_models, methods=["GET"], response_model=List[RealesrganItem])
        self.add_api_route("/sdapi/v1/prompt-styles", self.get_prompt_styles, methods=["GET"], response_model=List[PromptStyleItem])
        self.add_api_route("/sdapi/v1/artist-categories", self.get_artists_categories, methods=["GET"], response_model=List[str])
        self.add_api_route("/sdapi/v1/artists", self.get_artists, methods=["GET"], response_model=List[ArtistItem])
        self.add_api_route("/sdapi/v1/embeddings", self.get_embeddings, methods=["GET"], response_model=EmbeddingsResponse)
        self.add_api_route("/sdapi/v1/refresh-checkpoints", self.refresh_checkpoints, methods=["POST"])
        self.add_api_route("/sdapi/v1/create/embedding", self.create_embedding, methods=["POST"], response_model=CreateResponse)
        self.add_api_route("/sdapi/v1/create/hypernetwork", self.create_hypernetwork, methods=["POST"], response_model=CreateResponse)
        self.add_api_route("/sdapi/v1/preprocess", self.preprocess, methods=["POST"], response_model=PreprocessResponse)
        self.add_api_route("/sdapi/v1/train/embedding", self.train_embedding, methods=["POST"], response_model=TrainResponse)
        self.add_api_route("/sdapi/v1/train/hypernetwork", self.train_hypernetwork, methods=["POST"], response_model=TrainResponse)

        self.add_api_route("/user/create", self.create_new_user, methods=["POST"])
        self.add_api_route("/user/login", self.login, methods=["POST"])
        # self.add_api_route("/user/get_access_token", self.get_access_token, methods=["GET"])
        self.add_api_route("/user/reissue", self.reissue_access_token, methods=["POST"])
        self.add_api_route("/user/logout", self.logout, methods=["POST"])
        self.add_api_route("/user/read_user_info", self.read_user_info, methods=["GET"])
        self.add_api_route("/user/read/{user_id}", self.read_user_by_id, methods=["GET"])
        self.add_api_route("/user/read_all", self.read_all_users, methods=["GET"])
        self.add_api_route("/user/update_password", self.update_password, methods=["PUT"], response_model=UpdatePasswordResponse)
        self.add_api_route("/user/update_email", self.update_email, methods=["PUT"])
        self.add_api_route("/user/update_username", self.update_username, methods=["PUT"])
        self.add_api_route("/user/update/{user_id}", self.update_user_by_id, methods=["PUT"])
        self.add_api_route("/user/delete/{user_id}", self.delete_user_by_id, methods=["DELETE"])
        self.add_api_route("/user/make_admin/{user_id}", self.make_admin, methods=["PUT"])
        
        self.add_api_route("/credits/read/all", self.read_all_creds, methods=["GET"])
        self.add_api_route("/credits/read", self.read_cred_by_id, methods=["GET"])
        self.add_api_route("/credits/update", self.update_cred, methods=["PUT"])
        
        self.add_api_route("/imege/search", self.search_image, methods=["GET"])
        # self.add_api_route("/imege/delete", self.delete_image, methods=["DELETE"])
        
        @self.app.exception_handler(AuthJWTException)
        def authjwt_exception_handler(request: Request, exc: AuthJWTException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.message},
            )
        # self.add_api_route("/extra/res_codes", self.get_res_codes, methods=["GET"])
        
    # def get_res_codes(self):
    #     return {"response_codes": Responses.res_codes}
    
    def search_image(self, auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(auth)
        imezy_update_db = db.query(models.ImezyUpdateDB).filter(models.ImezyUpdateDB.email == auth['email'])
        if imezy_update_db is None:
            return {"image": None}
        
        response = []
        for i, row in enumerate(imezy_update_db):
            updated = datetime.strptime(row.updated, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M%S") # db의 updated 시간을 파일명에 맞게 변환
            
            # 이미지 저장된 json 파일 읽기
            with open(f"generated/{auth.email}/{updated}.json", "r") as f:
                data = json.load(f)
                
            if data["images"]:
                response.append({"images": data["images"], "updated": row.updated})
        return response
        
    
    def update_email(self, req: UpdateEmailRequest, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(user)
        print_message(f"req email: {req.email}, req confirm_email: {req.confirm_email}")
        if req.email != req.confirm_email:
            raise HTTPException(status_code=400, detail="Emails do not match")
        elif db.query(models.UsersDB).filter(models.UsersDB.email == req.email).first():
            raise HTTPException(status_code=400, detail="Email already exists")
        
        current_email = user['email']
        user_db = db.query(models.UsersDB).filter(models.UsersDB.email == current_email).first()
        if user_db is None:
            print_message(f"The token is invalid({current_email}). Please login again.")
            raise HTTPException(status_code=401, detail=f"The token is invalid({current_email}). Please login again.")
        user_db.email = req.email
        db.commit()

        try:
            access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRES_MINUTES)
            refresh_token_expires = timedelta(minutes=REFRESH_TOKEN_EXPIRES_MINUTES)
            access_token = create_access_token(email=user_db.email, user_id=user["user_id"], expires_delta=access_token_expires)
            refresh_token = create_refresh_token(email=user_db.email, user_id=user["user_id"], expires_delta=refresh_token_expires)
            response = {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
            response.update({"message": "Email updated to '{}' successfully".format(req.email)})
        except:
            db.rollback()
            raise HTTPException(status_code=500, detail="Could not update email")

        return response
    
    def update_username(self, req: UpdateUsernameRequest, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(user)
        if db.query(models.UsersDB).filter(models.UsersDB.username == req.username).first():
            raise HTTPException(status_code=400, detail="Username already exists")
        elif len(req.username) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters long")
        
        user_info = db.query(models.UsersDB).filter(models.UsersDB.id == user["user_id"]).first()
        if not user_info:
            raise exceptions.get_user_not_found_exception()
        
        user_info.username = req.username
        try:
            db.commit()
            print_message(f"Username updated to {req.username} successfully")
            return {"message": "Username updated to '{}' successfully".format(req.username)}
        except AttributeError:
            db.rollback()
            raise HTTPException(status_code=500, detail="Could not update username")
    
    def delete_user_by_id(self, user_id: int, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        
        not_authenticated_access_token(user)
        if db.query(models.UsersDB).filter(models.UsersDB.email == user["email"]).first().is_admin == False:
            print_message("User is not admin")
            raise exceptions.get_admin_exception()
        
        user_info = db.query(models.UsersDB).filter(models.UsersDB.id == user_id).first()
        username = user_info.username
        credits_info = db.query(models.CreditsDB).filter(models.CreditsDB.email == user_info.email).first()
        if user_info is not None:
            db.delete(credits_info)
            db.delete(user_info)
            db.commit()
            print_message(f"User {user_id}:{username} deleted")
            return {"message": f"User {user_id}:{username} deleted"}
        raise exceptions.get_user_not_found_exception()
    
    def user_update_history(self, user: dict, db: Session = Depends(get_db)):
        user_info = db.query(models.UsersDB).filter(models.UsersDB.email == user["email"]).first()
        user_info.last_login = datetime.datetime.now()
        db.commit()

    def update_user_by_id(self, user_id: int, user: UpdateUserRequest, db: Session = Depends(get_db)):
        return update_user(db, user_id, user)

    def read_all_users(self, db: Session = Depends(get_db)):
        return read_users(db)

    def read_user_by_id(self, user_id: int, 
                        user: dict = Depends(access_token_auth),
                        db: Session = Depends(get_db)):
        not_authenticated_access_token(user)
        user_info = db.query(models.UsersDB).filter(models.UsersDB.id == user_id).first()
        if user_info is not None:
            return user_info
        raise exceptions.get_user_not_found_exception()

    def read_user_info(self, user: dict = Depends(access_token_auth),
                         db: Session = Depends(get_db)):
        not_authenticated_access_token(user)
        
        try:
            user_info = db.query(models.UsersDB).filter(models.UsersDB.email == user["email"]).first().__dict__
        except AttributeError:
            raise HTTPException(status_code=404, detail=f"User not found with email {user['email']}")
        
        del user_info['hashed_password'], user_info['is_admin'], user_info['_sa_instance_state']
        
        user_info['credits'] = db.query(models.CreditsDB).filter(models.CreditsDB.email == user_info["email"]).first().__dict__["credits"]
        print_message(f'Read user info: {user_info["email"]}')
        return user_info

    def authenticate_user(self, email, password, db):
        user = db.query(models.UsersDB)\
            .filter(models.UsersDB.email == email).first()
        
        if not user:
            return False
        if not verify_password(password, user.hashed_password):
            return False
        return user
    
    def login(self, form_data: OAuth2PasswordRequestForm = Depends(),
                                 db: Session = Depends(get_db)):
        user = self.authenticate_user(form_data.email, form_data.password, db)
        if not user:
            raise exceptions.token_exception()
        access_token = create_access_token(email=user.email, user_id=user.id)
        refresh_token = create_refresh_token(email=user.email, user_id=user.id)
        
        former_rtoken = db.query(models.RefreshTokenDB).filter(models.RefreshTokenDB.email == user.email).first()
        # check if user has a refresh token is outdated
        if not former_rtoken:
            new_rtoken = models.RefreshTokenDB()
            new_rtoken.token = refresh_token
            new_rtoken.email = user.email
            
            db.add(new_rtoken)
            db.commit()
        else:
            former_rtoken.token = refresh_token
            db.commit()
        
        return {
            "access_token": access_token, 
            "refresh_token": refresh_token, 
            "token_type": "bearer"}
    
    # get new access token with refresh token
    def reissue_access_token(self, db: Session = Depends(get_db), auth: dict = Depends(refresh_token_auth)):
        if auth is None:
            raise HTTPException(status_code=400, detail="Invalid refresh token")
        
        user_db = db.query(models.UsersDB).filter(models.UsersDB.email == auth["email"]).first()
        if not user_db:
            raise exceptions.token_exception()
        
        rtoken = db.query(models.RefreshTokenDB).filter(models.RefreshTokenDB.email == user_db.email).first()
        if not rtoken:
            raise exceptions.token_exception()
        
        access_token = create_access_token(email=user_db.email, user_id=user_db.id)
        return {
            "access_token": access_token, 
            "token_type": "bearer"}
    
    # when user logs out, delete refresh token
    def logout(self, auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(auth)
        
        rtoken = db.query(models.RefreshTokenDB).filter(models.RefreshTokenDB.email == auth["email"]).first()
        if not rtoken:
            raise exceptions.token_exception()
        
        db.delete(rtoken)
        db.commit()
        return {"message": f"user {auth['email']} logged out"}
    
    def login_new(self, user: UserResponse, authorize: AuthJWT = Depends(), db: Session = Depends(get_db)):
        self.authenticate_user(user.email, user.password, db)
        user_db = db.query(models.UsersDB).filter(models.UsersDB.email == user.email).first()
        if user_db is None:
            raise exceptions.get_user_not_found_exception()
        elif not verify_password(user.password, user_db.hashed_password):
            raise exceptions.get_incorrent_password_exception
        elif not user_db.is_active:
            raise exceptions.get_not_active_user_exception()
            
        access_token = authorize.create_access_token(subject=user.email)
        refresh_token = authorize.create_refresh_token(subject=user.email)
        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
    
        

    def update_password(self, user: UpdatePasswordRequest, db: Session = Depends(get_db), auth: dict = Depends(access_token_auth)):
        if not auth:
            raise exceptions.get_user_exception()
        if auth["email"] != user.email:
            raise HTTPException(status_code=401, detail="Unauthorized user. Incorrect email")
        
        if user.new_password != user.confirm_password:
            raise HTTPException(status_code=400, detail="New password and confirm password do not match")
        elif not update_password(db, user): # update_password returns True if password was updated. it updates the password it self
            raise HTTPException(status_code=400, detail="Incorrect old password")
        
        return UpdatePasswordResponse(info="Password updated successfully")

    def create_new_user(self, create_user: CreateUserResponse, db: Session = Depends(get_db)):
        # filter if create_user.email or create_user.username already exists
        is_exist = []
        is_exist.append(db.query(models.UsersDB).filter(models.UsersDB.username == create_user.username).first())
        is_exist.append(db.query(models.UsersDB).filter(models.UsersDB.email == create_user.email.lower()).first())
        if is_exist[0]:
            print_message(f"The username '{create_user.username}' is already in use")
            raise HTTPException(status_code=400, detail=f"Username '{create_user.username}' is already in use")
        elif is_exist[1]:
            print_message(f"The email {create_user.email} is already in use")
            raise HTTPException(status_code=400, detail=f"The email {create_user.email} is already in use")
        
        create_user_model = models.UsersDB()
        create_user_model.email = create_user.email.lower()
        create_user_model.username = create_user.username
        create_user_model.is_active = create_user.is_active
        create_user_model.hashed_password = get_password_hashed(create_user.password)
        create_user_model.is_admin = create_user.is_admin
        print_message(f"Creating user: {create_user_model.email}, {create_user_model.username}")
        db.add(create_user_model)
        try:
            db.commit()
            print_message(f'User {create_user_model.email} created successfully')
            if create_user.is_admin:
                add_admin = models.UsersAdminDB()
                add_admin.email = create_user.email.lower()
                db.add(add_admin)
                db.commit()
        except exc.IntegrityError:
            db.rollback()
            print_message("User already exist")
            raise HTTPException(status_code=400, detail=f"The user {create_user_model.email} already exist")
        except FlushError:
            db.rollback()
            print_message(f"User already exist")
            raise HTTPException(status_code=400, detail=f"The user {create_user_model.email} already exist")
        
        
        new_credit = models.CreditsDB()
        new_credit.email = create_user_model.email
        db.add(new_credit)
        
        try:
            db.commit()
            print_message(f'Credits for user {create_user_model.email} created successfully')
        except exc.IntegrityError:
            db.rollback()
            db.query(models.UsersDB).filter(models.UsersDB.email == create_user_model.email).delete()
            db.commit()
            print_message(f"Failed to create credits for the user {create_user_model.email}")
            raise HTTPException(status_code=400, detail=f"Failed to create credits for the user {create_user_model.email}")
        except FlushError:
            db.rollback()
            db.query(models.UsersDB).filter(models.UsersDB.email == create_user_model.email).delete()
            db.commit()
            print_message(f"Failed to create credits for the user {create_user_model.email}")
            raise HTTPException(status_code=400, detail=f"Failed to create credits for the user {create_user_model.email}")

        return {"message": f"User {create_user.username} created successfully"}
    
    def update_credits(self, auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        if not auth:
            raise exceptions.get_user_exception()
        user = db.query(models.UsersDB).filter(models.UsersDB.email == auth["email"]).first()
        if user is None:
            raise exceptions.get_user_exception()
        user_credits = db.query(models.CreditsDB).filter(models.CreditsDB.user_id == user.id).first()
        if user_credits is None:
            raise exceptions.get_user_exception()
        user_credits.credits = user_credits.credits + user_credits.credits_inc
        db.commit()
        return {"message": f"Credits updated for user {user.username}"}
    
    def make_admin(self, user_id: int, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(user)
        if db.query(models.UsersDB).filter(models.UsersDB.email == user["email"]).first().is_admin == False:
            print_message("User is not admin")
            raise exceptions.get_admin_exception()
        
        user_info = db.query(models.UsersDB).filter(models.UsersDB.id == user_id).first()
        if user_info is not None:
            user_info.is_admin = True
            db.commit()
            print_message(f"User {user_id}:{user_info.username} is now admin")
            return {"message": f"User {user_id}:{user_info.username} is now admin"}
        raise HTTPException(status_code=404, detail="User not found")
    
    def add_api_route(self, path: str, endpoint, **kwargs):
        if shared.cmd_opts.api_auth:
            return self.app.add_api_route(path, endpoint, dependencies=[Depends(self.auth)], **kwargs)
        return self.app.add_api_route(path, endpoint, **kwargs)

    def auth(self, credentials: HTTPBasicCredentials = Depends(HTTPBasic())):
        if credentials.username in self.credentials:
            if compare_digest(credentials.password, self.credentials[credentials.username]):
                return True

        raise HTTPException(status_code=401, detail="Incorrect username or password", headers={"WWW-Authenticate": "Basic"})

    def text2imgapi(self, txt2imgreq: StableDiffusionTxt2ImgProcessingAPI):
        populate = txt2imgreq.copy(update={ # Override __init__ params
            "sampler_name": validate_sampler_name(txt2imgreq.sampler_name or txt2imgreq.sampler_index),
            "do_not_save_samples": True,
            "do_not_save_grid": True
            }
        )
        if populate.sampler_name:
            populate.sampler_index = None  # prevent a warning later on

        with self.queue_lock:
            p = StableDiffusionProcessingTxt2Img(sd_model=shared.sd_model, **vars(populate))

            shared.state.begin()
            processed = process_images(p)
            shared.state.end()


        b64images = list(map(encode_pil_to_base64, processed.images))

        return TextToImageResponse(images=b64images, parameters=vars(txt2imgreq), info=json.loads(processed.js()))

    def text2imgapi_auth(self, txt2imgreq: StableDiffusionTxt2ImgProcessingAPI, auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(auth)
        
        # check auth email and if the user has enough credits
        
        user_db = db.query(models.CreditsDB).filter(models.CreditsDB.email == auth["email"]).first()
        if user_db is None:
            print_message("user is None exception")
            raise exceptions.get_user_exception()
        created_images_num = int(txt2imgreq.n_iter * txt2imgreq.batch_size)
        
        # 유저가 가진 크레딧이 생성할 이미지의 크레딧보다 적으면 에러
        if user_db.credits < created_images_num * CREDITS_PER_IMAGE:
            raise exceptions.not_enough_credits_exception()
        
        response = self.text2imgapi(txt2imgreq)
        
        # 이미지 생성 저장(json)
        now = datetime.now().strftime('%Y%m%d%H%M%S')
        if os.path.exists(f"generated/t2i/{auth['email']}") == False:
            os.makedirs(f"generated/t2i/{auth['email']}")
        with open(f"generated/t2i/{auth['email']}/{now}.json", "w") as f:
            json.dump(json.loads(response.json()), f, indent=4)
            
        # 이미지 생성 데이터베이스 기록
        imezy_update_db = models.ImezyUpdateDB()
        imezy_update_db.email = auth['email']
        imezy_update_db.imezy_id = IMEZY_CONFIG["imezy_id"]['t2i']
        imezy_update_db.num_imgs = created_images_num
        imezy_update_db.updated = now
        db.add(imezy_update_db)

        # 크레딧 업데이트
        updateing_creedit_inc = -created_images_num *CREDITS_PER_IMAGE # 이미지당 10크레딧 차감
        if credits.update_cred(user_db.email, updateing_creedit_inc, db) == -1:
            print_message(f"{auth['email']}'s update_cred failed")
            raise exceptions.get_user_exception()
        
        print_message(f"User {auth['email']} is generating an image. Credits left: {user_db.credits}, Credits used: {-updateing_creedit_inc}, generated images: {created_images_num}")
            
        return response

    def img2imgapi(self, img2imgreq: StableDiffusionImg2ImgProcessingAPI):
        init_images = img2imgreq.init_images
        if init_images is None:
            raise HTTPException(status_code=404, detail="Init image not found")

        mask = img2imgreq.mask
        if mask:
            print_message(mask[:100])
            mask = decode_base64_to_image(mask)

        populate = img2imgreq.copy(update={ # Override __init__ params
            "sampler_name": validate_sampler_name(img2imgreq.sampler_name or img2imgreq.sampler_index),
            "do_not_save_samples": True,
            "do_not_save_grid": True,
            "mask": mask
            }
        )
        if populate.sampler_name:
            populate.sampler_index = None  # prevent a warning later on

        args = vars(populate)
        args.pop('include_init_images', None)  # this is meant to be done by "exclude": True in model, but it's for a reason that I cannot determine.

        with self.queue_lock:
            p = StableDiffusionProcessingImg2Img(sd_model=shared.sd_model, **args)
            p.init_images = [decode_base64_to_image(x) for x in init_images]

            shared.state.begin()
            processed = process_images(p)
            shared.state.end()

        b64images = list(map(encode_pil_to_base64, processed.images))

        if not img2imgreq.include_init_images:
            img2imgreq.init_images = None
            img2imgreq.mask = None

        return ImageToImageResponse(images=b64images, parameters=vars(img2imgreq), info=json.loads(processed.js()))

    def img2imgapi_auth(self, img2imgreq: StableDiffusionImg2ImgProcessingAPI, 
                        auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(auth)
        
        
        user_db = db.query(models.CreditsDB).filter(models.CreditsDB.email == auth["email"]).first()
        if user_db is None:
            print_message("user is None exception")
            raise exceptions.get_user_exception()
        created_images_num = int(img2imgreq.n_iter * img2imgreq.batch_size)
        
        # 유저가 가진 크레딧이 생성할 이미지의 크레딧보다 적으면 에러
        if user_db.credits < created_images_num * CREDITS_PER_IMAGE:
            raise exceptions.not_enough_credits_exception()
        
        response = self.img2imgapi(img2imgreq)
                    
        # save the response to the database
        now = datetime.now().strftime('%Y%m%d%H%M%S')
        if os.path.exists(f"generated/t2i/{auth['email']}") == False:
            os.makedirs(f"generated/t2i/{auth['email']}")
        with open(f"generated/t2i/{auth['email']}/{now}.json", "w") as f:
            json.dump(json.loads(response.json()), f, indent=4)

        # update credits
        updateing_creedit_inc = -created_images_num*CREDITS_PER_IMAGE # 10 credits per image
        if credits.update_cred(user_db.email, updateing_creedit_inc, db) == False:
            raise exceptions.get_user_exception()
        
        print_message(f"User {auth['email']} is generating an image. Credits left: {user_db.credits}")
            

        return response

    def extras_single_image_api(self, req: ExtrasSingleImageRequest):
        reqDict = setUpscalers(req)

        reqDict['image'] = decode_base64_to_image(reqDict['image'])

        with self.queue_lock:
            result = run_extras(extras_mode=0, image_folder="", input_dir="", output_dir="", save_output=False, **reqDict)

        return ExtrasSingleImageResponse(images=[encode_pil_to_base64(result[0][0])], html_info=result[1])
    
    def extras_single_image_api_auth(self, req: ExtrasSingleImageRequest, 
                                     auth: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        not_authenticated_access_token(auth)
        
        response = self.extras_single_image_api(req)
        
        return response

    def extras_batch_images_api(self, req: ExtrasBatchImagesRequest):
        reqDict = setUpscalers(req)

        def prepareFiles(file):
            file = decode_base64_to_file(file.data, file_path=file.name)
            file.orig_name = file.name
            return file

        reqDict['image_folder'] = list(map(prepareFiles, reqDict['imageList']))
        reqDict.pop('imageList')

        with self.queue_lock:
            result = run_extras(extras_mode=1, image="", input_dir="", output_dir="", save_output=False, **reqDict)

        return ExtrasBatchImagesResponse(images=list(map(encode_pil_to_base64, result[0])), html_info=result[1])

    def pnginfoapi(self, req: PNGInfoRequest):
        if(not req.image.strip()):
            return PNGInfoResponse(info="")

        result = run_pnginfo(decode_base64_to_image(req.image.strip()))

        return PNGInfoResponse(info=result[1])

    def progressapi(self, req: ProgressRequest = Depends()):
        # copy from check_progress_call of ui.py

        if shared.state.job_count == 0:
            return ProgressResponse(progress=0, eta_relative=0, state=shared.state.dict())

        # avoid dividing zero
        progress = 0.01

        if shared.state.job_count > 0:
            progress += shared.state.job_no / shared.state.job_count
        if shared.state.sampling_steps > 0:
            progress += 1 / shared.state.job_count * shared.state.sampling_step / shared.state.sampling_steps

        time_since_start = time.time() - shared.state.time_start
        eta = (time_since_start/progress)
        eta_relative = eta-time_since_start

        progress = min(progress, 1)

        shared.state.set_current_image()

        current_image = None
        if shared.state.current_image and not req.skip_current_image:
            current_image = encode_pil_to_base64(shared.state.current_image)

        return ProgressResponse(progress=progress, eta_relative=eta_relative, state=shared.state.dict(), current_image=current_image)

    def interrogateapi(self, interrogatereq: InterrogateRequest):
        image_b64 = interrogatereq.image
        if image_b64 is None:
            raise HTTPException(status_code=404, detail="Image not found")

        img = decode_base64_to_image(image_b64)
        img = img.convert('RGB')

        # Override object param
        with self.queue_lock:
            if interrogatereq.model == "clip":
                processed = shared.interrogator.interrogate(img)
            elif interrogatereq.model == "deepdanbooru":
                processed = deepbooru.model.tag(img)
            else:
                raise HTTPException(status_code=404, detail="Model not found")

        return InterrogateResponse(caption=processed)

    def interruptapi(self):
        shared.state.interrupt()

        return {}

    def skip(self):
        shared.state.skip()

    def get_config(self):
        options = {}
        for key in shared.opts.data.keys():
            metadata = shared.opts.data_labels.get(key)
            if(metadata is not None):
                options.update({key: shared.opts.data.get(key, shared.opts.data_labels.get(key).default)})
            else:
                options.update({key: shared.opts.data.get(key, None)})

        return options

    def set_config(self, req: Dict[str, Any]):
        for k, v in req.items():
            shared.opts.set(k, v)

        shared.opts.save(shared.config_filename)
        return

    def get_cmd_flags(self):
        return vars(shared.cmd_opts)

    def get_samplers(self):
        return [{"name": sampler[0], "aliases":sampler[2], "options":sampler[3]} for sampler in sd_samplers.all_samplers]

    def get_upscalers(self):
        upscalers = []

        for upscaler in shared.sd_upscalers:
            u = upscaler.scaler
            upscalers.append({"name":u.name, "model_name":u.model_name, "model_path":u.model_path, "model_url":u.model_url})

        return upscalers

    def get_sd_models(self):
        return [{"title":x.title, "model_name":x.model_name, "hash":x.hash, "filename": x.filename, "config": x.config} for x in checkpoints_list.values()]

    def get_hypernetworks(self):
        return [{"name": name, "path": shared.hypernetworks[name]} for name in shared.hypernetworks]

    def get_face_restorers(self):
        return [{"name":x.name(), "cmd_dir": getattr(x, "cmd_dir", None)} for x in shared.face_restorers]

    def get_realesrgan_models(self):
        return [{"name":x.name,"path":x.data_path, "scale":x.scale} for x in get_realesrgan_models(None)]

    def get_prompt_styles(self):
        styleList = []
        for k in shared.prompt_styles.styles:
            style = shared.prompt_styles.styles[k]
            styleList.append({"name":style[0], "prompt": style[1], "negative_prompt": style[2]})

        return styleList

    def get_artists_categories(self):
        return shared.artist_db.cats

    def get_artists(self):
        return [{"name":x[0], "score":x[1], "category":x[2]} for x in shared.artist_db.artists]
    
    def not_authenticated_access_token(self, auth: dict, db: Session = None):
        if db:
            if (user_db := db.query(UsersDB).filter(UsersDB.email == auth['email']).first()) is None:
                print_message("User is not in database")
                raise exceptions.get_user_exception()
        
        if auth is None:
            print_message("User is not authenticated")
            raise exceptions.get_user_exception()
        if auth['type'] == 'refresh':
            return False
    

    def get_embeddings(self):
        db = sd_hijack.model_hijack.embedding_db

        def convert_embedding(embedding):
            return {
                "step": embedding.step,
                "sd_checkpoint": embedding.sd_checkpoint,
                "sd_checkpoint_name": embedding.sd_checkpoint_name,
                "shape": embedding.shape,
                "vectors": embedding.vectors,
            }

        def convert_embeddings(embeddings):
            return {embedding.name: convert_embedding(embedding) for embedding in embeddings.values()}

        return {
            "loaded": convert_embeddings(db.word_embeddings),
            "skipped": convert_embeddings(db.skipped_embeddings),
        }

    def refresh_checkpoints(self):
        shared.refresh_checkpoints()

    def create_embedding(self, args: dict):
        try:
            shared.state.begin()
            filename = create_embedding(**args) # create empty embedding
            sd_hijack.model_hijack.embedding_db.load_textual_inversion_embeddings() # reload embeddings so new one can be immediately used
            shared.state.end()
            return CreateResponse(info = "create embedding filename: {filename}".format(filename = filename))
        except AssertionError as e:
            shared.state.end()
            return TrainResponse(info = "create embedding error: {error}".format(error = e))

    def create_hypernetwork(self, args: dict):
        try:
            shared.state.begin()
            filename = create_hypernetwork(**args) # create empty embedding
            shared.state.end()
            return CreateResponse(info = "create hypernetwork filename: {filename}".format(filename = filename))
        except AssertionError as e:
            shared.state.end()
            return TrainResponse(info = "create hypernetwork error: {error}".format(error = e))

    def preprocess(self, args: dict):
        try:
            shared.state.begin()
            preprocess(**args) # quick operation unless blip/booru interrogation is enabled
            shared.state.end()
            return PreprocessResponse(info = 'preprocess complete')
        except KeyError as e:
            shared.state.end()
            return PreprocessResponse(info = "preprocess error: invalid token: {error}".format(error = e))
        except AssertionError as e:
            shared.state.end()
            return PreprocessResponse(info = "preprocess error: {error}".format(error = e))
        except FileNotFoundError as e:
            shared.state.end()
            return PreprocessResponse(info = 'preprocess error: {error}'.format(error = e))

    def train_embedding(self, args: dict):
        try:
            shared.state.begin()
            apply_optimizations = shared.opts.training_xattention_optimizations
            error = None
            filename = ''
            if not apply_optimizations:
                sd_hijack.undo_optimizations()
            try:
                embedding, filename = train_embedding(**args) # can take a long time to complete
            except Exception as e:
                error = e
            finally:
                if not apply_optimizations:
                    sd_hijack.apply_optimizations()
                shared.state.end()
            return TrainResponse(info = "train embedding complete: filename: {filename} error: {error}".format(filename = filename, error = error))
        except AssertionError as msg:
            shared.state.end()
            return TrainResponse(info = "train embedding error: {msg}".format(msg = msg))

    def train_hypernetwork(self, args: dict):
        try:
            shared.state.begin()
            initial_hypernetwork = shared.loaded_hypernetwork
            apply_optimizations = shared.opts.training_xattention_optimizations
            error = None
            filename = ''
            if not apply_optimizations:
                sd_hijack.undo_optimizations()
            try:
                hypernetwork, filename = train_hypernetwork(*args)
            except Exception as e:
                error = e
            finally:
                shared.loaded_hypernetwork = initial_hypernetwork
                shared.sd_model.cond_stage_model.to(devices.device)
                shared.sd_model.first_stage_model.to(devices.device)
                if not apply_optimizations:
                    sd_hijack.apply_optimizations()
                shared.state.end()
            return TrainResponse(info = "train embedding complete: filename: {filename} error: {error}".format(filename = filename, error = error))
        except AssertionError as msg:
            shared.state.end()
            return TrainResponse(info = "train embedding error: {error}".format(error = error))

    def launch(self, server_name, port):
        self.app.include_router(self.router)
        uvicorn.run(self.app, host=server_name, port=port)

    def read_all_creds(self, db: Session = Depends(get_db)):
        return credits.read_creds(db)
    
    def read_cred_by_id(self, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        if user['type'] == 'refresh':
            return self.reissue_access_token(db=db, auth=user)
        
        not_authenticated_access_token(user)
        user_email = user.get("email", None)
        return credits.read_creds(db, user_email)
    
    
    def update_cred(self, request: UpdateCreditsRequest, user: dict = Depends(access_token_auth), db: Session = Depends(get_db)):
        if user['type'] == 'refresh':
            return self.reissue_access_token(db=db, auth=user)
        
        request = request.dict()
        user_admin_db = db.query(models.UsersAdminDB).filter(models.UsersAdminDB.email == user.get("email", None)).first()
        
        # if the user is not admin, he can only update his own credits
        if user_admin_db != None or request['email'] == user['email']:
            print_message("User is admin or updating his own credits")
            current_credits = credits.update_cred(request["email"], request["credits_inc"], db)
            return UpdateCreditsResponse(info = "Credits updated", email=request['email'], credits_inc=request['credits_inc'], currunt_credits=current_credits)
        else:
            raise HTTPException(status_code=403, detail="You are not authorized to update credits for this user")