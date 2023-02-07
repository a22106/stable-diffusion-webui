#!/bin/sh
python modules/duplicate.py -d1 ../stable-diffusion-webui-test/generated -d2 ../stable-diffusion-webui/generated # 생성 이미지 복사
python modules/duplicate.py -d1 ../stable-diffusion-webui-test/logs -d2 ../stable-diffusion-webui/logs # 로그 복사
python modules/duplicate.py -c -d1 ../stable-diffusion-webui-test/modules/api/conf -d2 ../stable-diffusion-webui/modules/api/conf # 설정 복사
python modules/duplicate.py -d1 ../stable-diffusion-webui-test/models -d2 ../stable-diffusion-webui/models # 모델 복사
pm2 start webui.py --name="webui" -f --interpreter python -- --listen --cors-allow-origins "*" --api --enable-insecure-extension-access --device-id 3