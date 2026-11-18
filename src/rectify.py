"""폰 사진에서 종이를 찾아 반듯하게 펴는 단계 (후보 A 앞단).

스캔과 달리 폰 사진은 책상 위에 비스듬히 놓인 종이를 찍어서 원근이 왜곡되고 배경이 섞인다.
1) 종이 후보를 여러 방법(밝기, 채도, GrabCut)으로 뽑는다
2) 후보의 네 변을 실제 경계(밝기가 급변하는 곳)로 당겨 맞춘다
3) 네 변이 모두 뚜렷한 경계 위에 놓인 후보만 인정하고, 그중 경계가 가장 뚜렷한 것을 고른다
4) 원근을 펴서 A4 세로 페이지(1240x1754)로 만든다  5) (선택) 조명 얼룩을 평평하게 보정한다
경계가 약하면 '못 찾음'으로 보고하고 원본을 그대로 돌려준다(틀린 모서리로 조용히 펴지 않는다).
"모양이 사각형인가"만 보고 고르면 어두운 책상은 맞아도 나무·베이지 책상에서 수십~수백 px 어긋났다(개발용 사진 측정).
모든 임계값은 RectifyParams 로 받는다. 개발용 사진으로만 정하고 시험용 사진은 보지 않았다.
"""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class RectifyParams:
    work_side: int = 900            # 종이를 찾을 때 줄여 쓰는 긴 변(px)
    blur: int = 7                   # 가우시안 블러 크기(홀수)
    close_kernel: int = 21          # 글자·괘선 구멍을 메우는 닫힘 연산 크기(줄인 이미지 기준)
    min_area_ratio: float = 0.25    # 종이가 사진에서 차지해야 하는 최소 비율
    max_area_ratio: float = 0.97    # 이보다 크면 배경과 구분하지 못한 것으로 본다
    snap_radius: float = 0.05       # 경계를 찾을 때 변에서 양쪽으로 살피는 거리(work_side 대비 비율)
    snap_samples: int = 40          # 변마다 경계를 재는 지점 수
    min_edge_strength: float = 4.0  # 네 변 모두에서 필요한 경계 세기(밝기 변화 단계/px의 중앙값)
    grabcut_iters: int = 4          # GrabCut 반복 횟수
    out_w: int = 1240               # 펴낸 페이지 크기(A4 150dpi 세로)
    out_h: int = 1754
    flat_kernel: int = 31           # 조명 보정: 글자를 지우는 최대값 필터 크기(펴낸 페이지 기준)
    flat_sigma: float = 40.0        # 조명 보정: 배경 밝기를 부드럽게 만드는 sigma(펴낸 페이지 기준)


def order_corners(pts):
    """네 점을 좌상, 우상, 우하, 좌하 순서로."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def _candidate_masks(small, p):
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (p.blur, p.blur), 0)
    masks = []
    _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(("밝기", m))
    # 종이는 색이 거의 없다: 채도가 낮은 부분도 후보로 쓴다(나무·베이지 책상은 채도가 높다)
    sat = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2HSV)[..., 1], (p.blur, p.blur), 0)
    _, ms = cv2.threshold(sat, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    masks.append(("채도", ms))
    # GrabCut: 사진 가장자리를 배경으로, 안쪽을 종이 후보로 두고 색 분포로 가른다
    cv2.setRNGSeed(0)
    h, w = small.shape[:2]
    gc = np.zeros((h, w), np.uint8)
    rect = (int(0.02 * w), int(0.02 * h), w - 2 * int(0.02 * w), h - 2 * int(0.02 * h))
    cv2.grabCut(small, gc, rect, np.zeros((1, 65)), np.zeros((1, 65)), p.grabcut_iters, cv2.GC_INIT_WITH_RECT)
    masks.append(("GrabCut", np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)))
    return masks


def _quad_from_mask(mask, p, min_area):
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (p.close_kernel, p.close_kernel))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.04, 0.06, 0.08):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order_corners(approx)
    return order_corners(cv2.boxPoints(cv2.minAreaRect(hull)))  # 사각형 근사에 실패: 가장 가까운 직사각형


def _side_profiles(gray, a, b, r, n_samples):
    """변 a->b 위의 지점마다 변에 수직인 방향으로 -r..+r 밝기 프로필을 뽑는다."""
    d = b - a
    t = d / max(float(np.linalg.norm(d)), 1e-6)
    n = np.array([-t[1], t[0]], np.float32)
    us = np.linspace(0.12, 0.88, n_samples, dtype=np.float32)
    ss = np.arange(-r, r + 1, dtype=np.float32)
    base = a[None, :] + us[:, None] * d[None, :]
    pts = base[:, None, :] + ss[None, :, None] * n[None, None, :]
    prof = cv2.remap(gray, pts[..., 0].astype(np.float32), pts[..., 1].astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return base, n, ss, prof


def _intersect(l1, l2):
    (p1, v1), (p2, v2) = l1, l2
    den = v1[0] * v2[1] - v1[1] * v2[0]
    if abs(den) < 1e-6:
        return None
    t = ((p2[0] - p1[0]) * v2[1] - (p2[1] - p1[1]) * v2[0]) / den
    return p1 + t * v1


def _snap(gray, quad, p, radii):
    """네 변을 실제 경계로 당긴다. 반환: (보정된 모서리, 네 변 중 가장 약한 경계 세기) 또는 (None, 0)."""
    strength = 0.0
    for r in radii:
        lines, strengths = [], []
        for i in range(4):
            base, n, ss, prof = _side_profiles(gray, quad[i], quad[(i + 1) % 4], r, p.snap_samples)
            g = np.abs(prof[:, 2:] - prof[:, :-2]) / 2.0
            # 처음 위치에서 가까운 경계를 더 쳐 준다: 종이 가장자리가 흐릿할 때 안쪽의 진한 표 테두리선을 경계로 오인하지 않게
            near = np.exp(-0.5 * (ss[1:-1] / (r / 2.0)) ** 2)
            k = (g * near[None, :]).argmax(axis=1)
            peak = g[np.arange(len(k)), k]
            pts = base + n[None, :] * ss[k + 1][:, None]
            keep = peak >= 0.5 * np.median(peak)
            if keep.sum() < 8:
                return None, 0.0
            vx, vy, x0, y0 = cv2.fitLine(pts[keep].astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
            lines.append((np.array([x0, y0], np.float32), np.array([vx, vy], np.float32)))
            strengths.append(float(np.median(peak)))
        corners = [_intersect(lines[(i - 1) % 4], lines[i]) for i in range(4)]
        if any(c is None for c in corners):
            return None, 0.0
        quad = np.array(corners, np.float32)
        strength = min(strengths)
    return quad, strength


def _valid(quad, shape, p):
    h, w = shape
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
        return False, 0.0
    area = cv2.contourArea(quad) / (h * w)
    if not (p.min_area_ratio <= area <= p.max_area_ratio):
        return False, area
    if (quad[:, 0].min() < -0.15 * w or quad[:, 0].max() > 1.15 * w or
            quad[:, 1].min() < -0.15 * h or quad[:, 1].max() > 1.15 * h):
        return False, area
    return True, area


def find_paper(img, p: RectifyParams):
    """종이 네 모서리(원본 좌표)와 정보. 못 찾으면 (None, 사유)."""
    h, w = img.shape[:2]
    scale = p.work_side / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0).astype(np.float32)
    min_area = p.min_area_ratio * small.shape[0] * small.shape[1]
    r1 = max(8, int(p.snap_radius * p.work_side))
    best, tried = None, []
    for name, mask in _candidate_masks(small, p):
        quad0 = _quad_from_mask(mask, p, min_area)
        if quad0 is None:
            tried.append(f"{name}: 후보 없음")
            continue
        quad, strength = _snap(gray, quad0, p, (r1, max(4, r1 // 4)))
        if quad is None:
            tried.append(f"{name}: 경계를 못 맞춤")
            continue
        ok, area = _valid(quad, gray.shape, p)
        tried.append(f"{name}: 경계 세기 {strength:.1f}" + ("" if ok else ", 모양 부적합"))
        if ok and (best is None or strength > best[0]):
            best = (strength, area, name, quad)
    if best is None or best[0] < p.min_edge_strength:
        top = "없음" if best is None else f"{best[0]:.1f}"
        return None, f"종이를 찾지 못함(경계가 뚜렷한 후보 없음. 최고 경계 세기 {top} < {p.min_edge_strength}; {'; '.join(tried)})"
    strength, area, name, quad = best
    return quad / scale, {"방법": name, "종이 비율": round(float(area), 3), "경계 세기": round(float(strength), 1)}


def flatten(img, p: RectifyParams):
    """조명 얼룩 보정: 채널마다 배경(종이) 밝기를 추정해 나눈다."""
    out = np.empty_like(img)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (p.flat_kernel, p.flat_kernel))
    for c in range(3):
        ch = img[..., c].astype(np.float32)
        bg = cv2.GaussianBlur(cv2.dilate(ch, k), (0, 0), p.flat_sigma)
        out[..., c] = np.clip(ch / np.maximum(bg, 1.0) * 255.0, 0, 255).astype(np.uint8)
    return out


def rectify(img, p: RectifyParams, flatfield=False):
    """사진 -> 반듯하게 편 A4 세로 페이지. 반환: (이미지, 정보). 못 찾으면 원본과 사유."""
    quad, info = find_paper(img, p)
    if quad is None:
        return img, {"찾음": False, "사유": info}
    quad = order_corners(quad)
    tl, tr, br, bl = quad
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    if width > height:  # 가로로 찍힌 사진: 긴 변이 세로가 되게 모서리 이름을 돌린다
        quad = np.array([bl, tl, tr, br], np.float32)
        info["가로 사진"] = True
    dst = np.array([[0, 0], [p.out_w - 1, 0], [p.out_w - 1, p.out_h - 1], [0, p.out_h - 1]], np.float32)
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    out = cv2.warpPerspective(img, M, (p.out_w, p.out_h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    info.update({"찾음": True, "모서리": [[round(float(x), 1), round(float(y), 1)] for x, y in quad]})
    if flatfield:
        out = flatten(out, p)
    return out, info
