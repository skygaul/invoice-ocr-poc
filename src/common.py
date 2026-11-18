"""후보 A·B·C가 함께 쓰는 도구: PDF 렌더링, 실행 폴더, 네트워크 차단, 확인용 그림, 예측 파일 저장.

원칙(노트북 지시문): 값은 전부 인자로 받는다 / 점수와 신뢰도는 반올림하지 않는다 /
덮어쓰지 않는다 / 결과를 눈으로 볼 수 있게 함께 저장한다 / 자료를 외부로 보내지 않는다.
"""
import datetime
import json
import os
import socket
import sys

import numpy as np

DPI = 150  # 박스 좌표는 이 해상도의 픽셀(A4 = 1240 x 1754)로 기록한다.
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}
NET_ATTEMPTS = []


def block_network():
    """파이썬 소켓으로 나가는 외부 연결을 막고, 시도한 것을 기록한다(로컬 주소는 허용).
    파이썬 밖(C++)에서 나가는 연결까지는 막지 못한다. 그 한계는 docs에 적는다."""
    os.environ.update({
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True", "HF_HUB_DISABLE_TELEMETRY": "1",
    })
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def host_of(addr):
        return addr[0] if isinstance(addr, tuple) else str(addr)

    def guarded_connect(self, addr):
        h = host_of(addr)
        if h not in LOCAL_HOSTS and not str(h).startswith("127."):
            NET_ATTEMPTS.append(f"connect {h}")
            raise OSError(f"외부 연결 차단: {h}")
        return real_connect(self, addr)

    def guarded_connect_ex(self, addr):
        h = host_of(addr)
        if h not in LOCAL_HOSTS and not str(h).startswith("127."):
            NET_ATTEMPTS.append(f"connect_ex {h}")
            return 10061
        return real_connect_ex(self, addr)

    def guarded_getaddrinfo(host, *a, **k):
        if host not in LOCAL_HOSTS and not str(host).startswith("127."):
            NET_ATTEMPTS.append(f"dns {host}")
            raise socket.gaierror(f"외부 이름 조회 차단: {host}")
        return real_getaddrinfo(host, *a, **k)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo


def list_pdfs(folder):
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf"))


def render_pdf(path, dpi=DPI):
    """PDF 첫 장을 RGB 배열로 만든다. (PyMuPDF는 AGPL: 제품 배포 전에 라이선스를 정해야 함)"""
    try:
        import pymupdf as fitz  # 최신 이름. 옛 버전은 fitz
    except ImportError:
        import fitz
    with fitz.open(path) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), colorspace=fitz.csRGB)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def new_run_dir(candidate, root="outputs"):
    """outputs/<후보>/<실행시각> 새 폴더. 이미 있으면 거부한다."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = os.path.join(root, candidate, stamp)
    os.makedirs(d, exist_ok=False)
    os.makedirs(os.path.join(d, "overlay"), exist_ok=False)
    return d


def save_run(run_dir, preds, params, extra_log=None):
    with open(os.path.join(run_dir, "predictions.json"), "w", encoding="utf-8") as f:
        json.dump(preds, f, ensure_ascii=False, indent=1)
    meta = {"params": params, "python": sys.version.split()[0],
            "network_attempts_blocked": NET_ATTEMPTS, "log": extra_log or {}}
    with open(os.path.join(run_dir, "params.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def draw_overlay(img, pred, path, low_conf=None):
    """추출한 값의 박스를 원본 위에 그려 저장한다.
    초록 = 그대로 통과, 빨강 = 사람 확인 필요, 파랑 = 박스 없음(그릴 수 없음)."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(img).convert("RGB")
    dr = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/malgun.ttf", 20)
    except OSError:
        f = ImageFont.load_default()

    def box(b, review, label):
        col = (200, 30, 30) if review else (20, 140, 40)
        if b and len(b) == 4:
            dr.rectangle(b, outline=col, width=3)
            dr.text((b[0], max(0, b[1] - 24)), label, fill=col, font=f)

    for name, fld in (pred.get("fields") or {}).items():
        if isinstance(fld, dict):
            box(fld.get("box"), fld.get("review"), f"{name}")
    for i, it in enumerate(pred.get("items") or []):
        box(it.get("box"), it.get("review"), f"{i + 1}")
    im.save(path)


def to_number(s):
    """OCR 문자열에서 숫자 하나를 뽑는다. 못 읽으면 None. 소수점 한 자리까지 허용(2.5kg)."""
    import re
    if s is None:
        return None
    t = str(s).replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group())
    return int(v) if v.is_integer() else v
