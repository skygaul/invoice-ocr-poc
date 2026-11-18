"""종이 펴기(rectify) 검증. 알려진 원근 변환으로 사진을 만들어 모서리와 결과 이미지를 확인한다.
실행: python -X utf8 -m unittest src.test_rectify -v   (프로젝트 폴더에서)
"""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import common
import rectify as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = common.render_pdf(os.path.join(ROOT, "sample-invoices", "명세서_01.pdf"))
W, H = 1620, 2160
QUAD = np.array([[160, 230], [1480, 180], [1560, 2050], [110, 1960]], np.float32)  # 좌상, 우상, 우하, 좌하
P = R.RectifyParams()


def photo(quad=QUAD, bg=(62, 62, 66), size=(W, H), page=PAGE):
    ph, pw = page.shape[:2]
    src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], np.float32)
    M = cv2.getPerspectiveTransform(src, quad)
    canvas = np.ones((size[1], size[0], 3), np.uint8) * np.array(bg, np.uint8)
    warped = cv2.warpPerspective(page, M, size)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), M, size)
    canvas[mask > 127] = warped[mask > 127]
    return canvas


def corner_error(info, quad=QUAD):
    got = np.array(info["모서리"], np.float32)
    return float(np.linalg.norm(got - quad, axis=1).mean())


def patch_match(out, ref, box=(300, 1300, 200, 1000)):
    """편 페이지의 가운데 조각이 원본 어디에서 얼마나 닮았는지: (최대 상관, x 어긋남, y 어긋남).
    전체 이미지 상관은 수 픽셀만 어긋나도 글자 줄 단위로 크게 떨어져서 기준으로 쓰지 않는다."""
    y0, y1, x0, x1 = box
    g = lambda im: cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
    res = cv2.matchTemplate(g(ref), g(out)[y0:y1, x0:x1], cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc[0] - x0, loc[1] - y0


class RectifyTests(unittest.TestCase):
    def test_order_corners_from_any_order(self):
        pts = QUAD[[2, 0, 3, 1]]
        self.assertTrue(np.allclose(R.order_corners(pts), QUAD))

    def test_dark_desk_corners_and_page_recovered(self):
        out, info = R.rectify(photo(), P)
        self.assertTrue(info["찾음"], info)
        self.assertLess(corner_error(info), 15)
        self.assertEqual(out.shape[:2], (P.out_h, P.out_w))
        corr, dx, dy = patch_match(out, cv2.resize(PAGE, (P.out_w, P.out_h)))
        self.assertGreater(corr, 0.8)
        self.assertLessEqual(max(abs(dx), abs(dy)), 4)

    def test_wood_and_light_beige_desks(self):
        for bg in ((150, 110, 76), (206, 192, 168)):
            out, info = R.rectify(photo(bg=bg), P)
            self.assertTrue(info["찾음"], (bg, info))
            self.assertLess(corner_error(info), 25, bg)

    def test_strong_perspective_and_rotation(self):
        quad = np.array([[330, 300], [1380, 160], [1590, 2090], [60, 1850]], np.float32)
        out, info = R.rectify(photo(quad), P)
        self.assertTrue(info["찾음"], info)
        self.assertLess(corner_error(info, quad), 30)

    def test_no_paper_returns_original_and_says_why(self):
        blank = np.ones((H, W, 3), np.uint8) * 120
        out, info = R.rectify(blank, P)
        self.assertFalse(info["찾음"])
        self.assertIn("찾지 못함", info["사유"])
        self.assertIs(out, blank)  # 조용히 바꾸지 않고 원본 그대로

    def test_paper_too_small_is_not_accepted(self):
        quad = np.array([[600, 800], [900, 800], [900, 1300], [600, 1300]], np.float32)
        out, info = R.rectify(photo(quad), P)
        self.assertFalse(info["찾음"])

    def test_landscape_photo_becomes_portrait_page(self):
        rot = cv2.rotate(photo(), cv2.ROTATE_90_COUNTERCLOCKWISE)
        out, info = R.rectify(rot, P)
        self.assertTrue(info["찾음"], info)
        self.assertTrue(info.get("가로 사진"))
        self.assertEqual(out.shape[:2], (P.out_h, P.out_w))  # 위아래(글자 방향)는 맞추지 않는다

    def test_deterministic(self):
        a, _ = R.rectify(photo(), P)
        b, _ = R.rectify(photo(), P)
        self.assertTrue(np.array_equal(a, b))

    def test_flatfield_removes_a_lighting_gradient(self):
        page = cv2.resize(PAGE, (P.out_w, P.out_h)).astype(np.float32)
        grad = np.linspace(0.45, 1.0, P.out_w, dtype=np.float32)[None, :, None]
        lit = np.clip(page * grad, 0, 255).astype(np.uint8)
        flat = R.flatten(lit, P)
        # 글자가 거의 없는 아래쪽 빈 종이의 열 평균 밝기가 좌우로 얼마나 다른가
        band = lambda im: im[1300:1700].mean(axis=(0, 2))
        self.assertGreater(band(lit).std(), 15)
        self.assertLess(band(flat).std(), band(lit).std() * 0.2)

    def test_flatfield_keeps_the_text_dark(self):
        page = cv2.resize(PAGE, (P.out_w, P.out_h))
        flat = R.flatten(page, P)
        g = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY)
        self.assertLess(np.percentile(g, 1), 90)   # 글자는 여전히 어둡다
        self.assertGreater(np.percentile(g, 60), 200)  # 종이는 밝다


if __name__ == "__main__":
    unittest.main()
