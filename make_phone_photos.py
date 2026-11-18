"""깨끗한 명세서 페이지 이미지에서 '폰으로 찍은 사진'을 흉내 낸 JPEG 세트를 만든다. 실제 촬영이 아니다.

사용: python -X utf8 make_phone_photos.py <깨끗한 PNG 폴더> <출력 폴더> <시드>
깨끗한 PNG는 python make_invoices.py <임시출력> - <시드> <PNG 폴더> 로 만든다.

서류마다 촬영 조건의 심한 정도 s 를 U(0.15, 1.0)에서 뽑고(시드로 고정), 아래 효과를 s에 비례해 입힌다.
같은 문서 내용이 스캔과 사진에서 어떻게 달라지는지 나란히 보려고 서류 번호와 정답은 스캔 세트와 같다.
- 종이를 책상 위에 놓고 비스듬히 찍은 원근 왜곡(위쪽이 좁아지는 키스톤 포함)과 기울기
- 책상 배경(어두운 책상, 나무, 밝은 베이지 중 하나. 밝은 배경은 종이와 구분하기 어렵다)
- 그림자(부드러운 얼룩, 한쪽 방향 그라디언트, 손·폰 그림자 띠), 빛 반사(번쩍임)
- 노출 부족, 색 치우침, 초점 흐림, 약한 손떨림, 센서 노이즈, 모서리 어두워짐, JPEG 압축
모든 조건은 manifest.json에 기록한다(종이 네 모서리의 사진 속 좌표 포함).
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

CANVAS_W, CANVAS_H = 1620, 2160  # 약 3.5MP. 실제 폰(12MP)보다 작지만 종이 폭이 스캔(1240px)과 비슷하게 나온다.
PAPER_RATIO = 1754 / 1240
BACKGROUNDS = {"어두운 책상": (62, 62, 66), "나무": (150, 110, 76), "밝은 베이지": (206, 192, 168)}
BG_WEIGHTS = [0.40, 0.35, 0.25]


def read_png(path):
    arr = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def smooth_field(rng, h, w, cells=(5, 4)):
    """낮은 해상도의 난수를 부드럽게 키운 얼룩(0~1)."""
    small = rng.random((cells[1], cells[0])).astype(np.float32)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def background(rng, name):
    base = np.array(BACKGROUNDS[name], np.float32)
    bg = np.ones((CANVAS_H, CANVAS_W, 3), np.float32) * base
    bg *= (0.9 + 0.2 * smooth_field(rng, CANVAS_H, CANVAS_W, (4, 5)))[..., None]
    if name == "나무":  # 나뭇결: 가로로 긴 가는 줄무늬
        rows = rng.normal(0, 7, (CANVAS_H, 1)).astype(np.float32)
        rows = cv2.GaussianBlur(rows, (1, 0), 3)
        bg += rows[:, :, None]
    bg += rng.normal(0, 3, bg.shape).astype(np.float32)
    return bg


def paper_quad(rng, s):
    """사진 속 종이 네 모서리(좌상, 우상, 우하, 좌하)."""
    pw = CANVAS_W * rng.uniform(0.86 - 0.14 * s, 0.90 - 0.06 * s)
    ph = pw * PAPER_RATIO
    if ph > CANVAS_H * 0.94:
        ph, pw = CANVAS_H * 0.94, CANVAS_H * 0.94 / PAPER_RATIO
    cx = CANVAS_W / 2 + rng.uniform(-0.04, 0.04) * CANVAS_W
    cy = CANVAS_H / 2 + rng.uniform(-0.03, 0.03) * CANVAS_H
    quad = np.array([[-pw / 2, -ph / 2], [pw / 2, -ph / 2], [pw / 2, ph / 2], [-pw / 2, ph / 2]], np.float32)
    # 위쪽이 좁아지는 키스톤(폰을 기울여 찍음)
    k = rng.uniform(0, 0.10 * s) * pw
    quad[0, 0] += k
    quad[1, 0] -= k
    # 모서리마다 따로 흔들기
    jx, jy = (0.008 + 0.06 * s) * pw, (0.008 + 0.06 * s) * ph
    quad += np.stack([rng.uniform(-jx, jx, 4), rng.uniform(-jy, jy, 4)], axis=1).astype(np.float32)
    # 전체 회전
    th = np.deg2rad(rng.uniform(-1, 1) * (2.5 + 9.0 * s))
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]], np.float32)
    quad = quad @ R.T + np.array([cx, cy], np.float32)
    return quad


def shade(rng, img, s, info):
    h, w = img.shape[:2]
    # 얼룩 그림자
    a = 0.05 + 0.28 * s
    img *= (1 - a * smooth_field(rng, h, w, (5, 6)))[..., None]
    info["그림자 세기"] = round(float(a), 3)
    # 한쪽 방향 그라디언트(조명이 한쪽에서 옴)
    ang = rng.uniform(0, 2 * np.pi)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    g = ((xx / w - 0.5) * np.cos(ang) + (yy / h - 0.5) * np.sin(ang))
    img *= (1 - (0.05 + 0.30 * s) * (g + 0.5))[..., None]
    # 손·폰 그림자 띠
    info["손 그림자"] = bool(rng.random() < 0.15 + 0.5 * s)
    if info["손 그림자"]:
        edge = rng.integers(0, 4)
        band = np.zeros((h, w), np.float32)
        t = int((0.10 + 0.10 * s) * (w if edge < 2 else h))
        if edge == 0: band[:, :t] = 1
        elif edge == 1: band[:, w - t:] = 1
        elif edge == 2: band[:t, :] = 1
        else: band[h - t:, :] = 1
        band = cv2.GaussianBlur(band, (0, 0), 0.06 * w)
        img *= (1 - (0.20 + 0.25 * s) * band)[..., None]
    # 빛 반사
    info["빛 반사"] = bool(rng.random() < 0.45 * s)
    if info["빛 반사"]:
        gx, gy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.2, 0.7) * h
        sig = 0.10 * w
        blob = np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * sig ** 2)))
        img += (255 * (0.25 + 0.35 * s) * blob)[..., None]
    # 모서리 어두워짐
    vig = 1 - (0.05 + 0.20 * s) * np.clip(np.sqrt(((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2)) * 1.6, 0, 1) ** 2
    img *= vig[..., None]
    return img


def make_photo(clean, rng, s):
    info = {"심한 정도 s": round(float(s), 3)}
    quad = paper_quad(rng, s)
    ph, pw = clean.shape[:2]
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    H = cv2.getPerspectiveTransform(src, quad)
    paper = cv2.warpPerspective(clean.astype(np.float32), H, (CANVAS_W, CANVAS_H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones((ph, pw), np.float32), H, (CANVAS_W, CANVAS_H), flags=cv2.INTER_LINEAR)
    mask = cv2.GaussianBlur(mask, (0, 0), 1.0)[..., None]
    bg_name = rng.choice(list(BACKGROUNDS), p=BG_WEIGHTS)
    img = background(rng, bg_name) * (1 - mask) + paper * mask
    # 종이 가장자리 그림자(종이가 책상에서 살짝 뜸)
    edge = cv2.GaussianBlur(mask[..., 0], (0, 0), 6)
    img *= (1 - 0.10 * np.clip(edge - mask[..., 0], 0, 1))[..., None]
    img = shade(rng, img, s, info)
    # 노출과 색 치우침
    gain = rng.uniform(1 - 0.32 * s, 1.0)
    cast = 1 + rng.uniform(-1, 1, 3) * (0.03 + 0.06 * s)
    img *= gain * cast
    info.update({"배경": str(bg_name), "노출 배율": round(float(gain), 3)})
    # 초점 흐림과 약한 손떨림
    sigma = 0.4 + 1.6 * s * rng.uniform(0.5, 1.0)
    img = cv2.GaussianBlur(img, (0, 0), sigma)
    info["흐림 sigma"] = round(float(sigma), 2)
    info["손떨림"] = bool(rng.random() < 0.25 * s)
    if info["손떨림"]:
        n = int(rng.integers(3, 8))
        kern = np.zeros((n, n), np.float32)
        kern[n // 2, :] = 1
        M = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), float(rng.uniform(0, 180)), 1)
        kern = cv2.warpAffine(kern, M, (n, n))
        img = cv2.filter2D(img, -1, kern / max(kern.sum(), 1e-6))
    # 센서 노이즈와 JPEG
    noise = 2 + 8 * s
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    img += rng.normal(0, noise * 0.5, (img.shape[0], img.shape[1], 1)).astype(np.float32)
    info["노이즈 sigma"] = round(float(noise), 2)
    info["JPEG 품질"] = int(88 - 18 * s)
    info["종이 모서리(좌상,우상,우하,좌하)"] = [[round(float(x), 1), round(float(y), 1)] for x, y in quad]
    return np.clip(img, 0, 255).astype(np.uint8), info


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    src_dir, out_dir, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
    if os.path.exists(out_dir) and os.listdir(out_dir):
        sys.exit(f"비어 있지 않은 폴더라 덮어쓰지 않습니다: {out_dir}")
    os.makedirs(out_dir, exist_ok=True)
    manifest = {}
    for name in sorted(os.listdir(src_dir)):
        if not name.lower().endswith(".png"):
            continue
        no = int("".join(ch for ch in name if ch.isdigit()))
        rng = np.random.default_rng(seed * 1000 + no)
        s = rng.uniform(0.15, 1.0)
        photo, info = make_photo(read_png(os.path.join(src_dir, name)), rng, s)
        stem = os.path.splitext(name)[0]
        Image.fromarray(photo).save(os.path.join(out_dir, stem + ".jpg"), quality=info["JPEG 품질"])
        manifest[stem + ".jpg"] = info
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    sv = sorted(v["심한 정도 s"] for v in manifest.values())
    print(f"{len(manifest)}장 저장: {out_dir} | s 최소 {sv[0]} 중앙 {sv[len(sv)//2]} 최대 {sv[-1]}")


if __name__ == "__main__":
    main()
