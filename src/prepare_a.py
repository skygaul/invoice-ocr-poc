"""후보 A 모델 준비(한 번만). 이 스크립트만 네트워크를 쓴다. 실행 스크립트(run_a.py)는 외부 연결을 막고 돈다.

받는 것(모두 Hugging Face 공식 PaddlePaddle 저장소, 카드의 라이선스 apache-2.0, 2026-11-17 확인):
  - PaddlePaddle/PP-OCRv5_server_det          (글자 위치 검출, 약 88MB)
  - PaddlePaddle/korean_PP-OCRv5_mobile_rec   (한국어 글자 인식, 약 14MB)
저장소가 나중에 갱신돼도 같은 결과가 나오도록 **리비전(커밋)을 고정**하고, 받은 뒤 가중치 파일의 SHA-256을 확인한다.
저장 위치는 PaddleX 모델 캐시(기본 ~/.paddlex, 환경변수 PADDLE_PDX_CACHE_HOME으로 바꿀 수 있음)의 official_models 폴더다.

사용: python -X utf8 src/prepare_a.py
"""
import hashlib
import os
import sys

MODELS = {
    "PP-OCRv5_server_det": {
        "revision": "ca867c897ecbca8873081573a802ad70d499cb94",
        "sha256": "183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d",
    },
    "korean_PP-OCRv5_mobile_rec": {
        "revision": "c02ecaf1f22bfd1c618cce154fd19185b47e663a",
        "sha256": "cac3e5f12cf04aaa77f6a5bc704e4e736ef2908476551891d84b41b4e9090462",
    },
}


def cache_root():
    base = os.environ.get("PADDLE_PDX_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".paddlex")
    return os.path.abspath(os.path.join(base, "official_models"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    from huggingface_hub import snapshot_download

    root = cache_root()
    os.makedirs(root, exist_ok=True)
    ok = True
    for name, info in MODELS.items():
        target = os.path.join(root, name)
        snapshot_download(repo_id=f"PaddlePaddle/{name}", revision=info["revision"], local_dir=target)
        got = sha256(os.path.join(target, "inference.pdiparams"))
        match = got == info["sha256"]
        ok &= match
        print(f"{name}: 리비전 {info['revision'][:10]}… 가중치 해시 {'일치' if match else '불일치 ' + got}")
    if not ok:
        sys.exit("가중치 해시가 다릅니다. 저장소가 바뀌었거나 내려받기가 깨졌습니다.")
    print("준비 완료:", root)


if __name__ == "__main__":
    main()
