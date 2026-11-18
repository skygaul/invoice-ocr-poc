"""후보 B 모델 준비(한 번만). 이 스크립트만 네트워크를 쓴다. 실행 스크립트(run_b.py)는 네트워크를 막고 돈다.

사용자가 내려받기를 허락한 것(2026-11-17):
  - PaddlePaddle/PaddleOCR-VL-1.6  (가중치 약 1.9GB, Apache-2.0 카드 확인)
  - PaddlePaddle/PP-DocLayoutV3     (약 132MB, Apache-2.0 카드 확인)
이 둘 외의 모델은 받지 않는다. 문서 방향 분류·왜곡 보정 모델은 끄고 받지 않는다.
"""
import os
import sys

os.environ["PADDLE_PDX_MODEL_SOURCE"] = "huggingface"
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

from paddleocr import PaddleOCRVL

pipe = PaddleOCRVL(pipeline_version="v1.6", device="cpu",
                   use_doc_orientation_classify=False, use_doc_unwarping=False)
root = os.path.join(os.path.expanduser("~"), ".paddlex", "official_models")
for name in sorted(os.listdir(root)):
    p = os.path.join(root, name)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(p) for f in fs)
    print(f"{name}: {size / 1e6:.0f} MB")
print("준비 완료")
