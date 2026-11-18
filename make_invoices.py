"""거래명세서 샘플 20장(스캔 PDF) + 정답 파일 생성.
사용: python -X utf8 make_invoices.py [출력폴더] [미리보기폴더|-] [시드] [깨끗한 원본 PNG 폴더]
시드를 생략하면 시험용 20장(sample-invoices)과 같은 값이 나온다.
개발용 샘플은 다른 시드로 만든다(예: 7). 시험용으로는 개발에 쓰지 않는다.
모든 상호·번호·인명은 가상입니다.
"""
import csv, json, math, os, random, sys, io
from datetime import date, timedelta
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = sys.argv[1] if len(sys.argv) > 1 else "sample-invoices"
PREVIEW = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 20261117
CLEAN_DIR = sys.argv[4] if len(sys.argv) > 4 else None  # 스캔 효과를 입히기 전의 깨끗한 페이지 이미지(PNG)도 저장
os.makedirs(OUT, exist_ok=True)
if PREVIEW:
    os.makedirs(PREVIEW, exist_ok=True)
if CLEAN_DIR:
    os.makedirs(CLEAN_DIR, exist_ok=True)

random.seed(SEED)
np.random.seed(SEED)

W, H = 1240, 1754  # A4 @150dpi
FD = "C:/Windows/Fonts/"


def font(name, size):
    if name == "batang":
        return ImageFont.truetype(FD + "batang.ttc", size, index=0)
    if name == "gulim":
        return ImageFont.truetype(FD + "gulim.ttc", size, index=0)
    if name == "bold":
        return ImageFont.truetype(FD + "malgunbd.ttf", size)
    if name == "hand":
        return ImageFont.truetype(FD + "Inkfree.ttf", size)
    return ImageFont.truetype(FD + "malgun.ttf", size)


# 공급사: (상호, 대표, 품목풀) / 품목: (이름, 단위, 단가, 수량후보, 과세여부)
S = {
    "한울닭유통": ("한울닭유통", "김한울", [
        ("닭다리살", "kg", 8200, [5, 8, 10, 12], 0), ("닭목살", "kg", 9500, [3, 5, 6], 0),
        ("닭안심", "kg", 7800, [3, 5], 0), ("닭날개", "kg", 6900, [5, 8, 10], 0),
        ("닭염통", "kg", 7200, [2, 3, 4], 0), ("닭껍질", "kg", 5500, [3, 4, 5], 0),
        ("닭모래집", "kg", 6800, [2, 3], 0), ("닭무릎연골", "kg", 14000, [1, 2, 3], 0),
        ("닭가슴연골", "kg", 13500, [1, 2], 0), ("닭완자(츠쿠네용)", "kg", 9800, [2, 3, 4], 0)]),
    "초록마을농산": ("초록마을농산", "박초록", [
        ("대파", "단", 3800, [5, 8, 10], 0), ("오이", "박스", 22000, [1, 2], 0),
        ("양배추", "통", 4200, [5, 10], 0), ("연근", "kg", 6500, [3, 5], 0),
        ("레몬", "개", 700, [20, 30, 50], 0), ("깻잎", "속", 1500, [10, 20], 0),
        ("무", "개", 2500, [3, 5], 0), ("양파", "kg", 1800, [10, 20], 0)]),
    "바다한상수산": ("바다한상수산", "이바다", [
        ("칵테일새우", "kg", 15000, [2, 3, 5], 0), ("홍합", "kg", 4500, [5, 8, 10], 0),
        ("모둠해물", "kg", 18000, [2, 3], 0), ("오징어", "마리", 3500, [5, 8], 0)]),
    "정육명가": ("정육명가", "최명가", [
        ("한우대창", "kg", 32000, [2, 3, 5], 0), ("사골육수", "팩", 4800, [10, 20, 30], 0),
        ("돼지삼겹", "kg", 13000, [3, 5], 0), ("소곱창", "kg", 30000, [2, 3], 0)]),
    "면가원식품": ("면가원식품", "정면가", [
        ("우동면", "봉", 1200, [20, 30, 40], 1), ("소바면", "봉", 1300, [20, 30], 1),
        ("계란(특란)", "판", 6500, [4, 6, 8], 0), ("가쓰오부시", "봉", 5800, [3, 5], 1),
        ("빵가루", "kg", 2800, [5, 10], 1), ("튀김가루", "kg", 2400, [5, 10], 1),
        ("타르타르소스 (업소용 대용량 1.8L)", "통", 9800, [2, 3], 1),
        ("야끼소바소스 (업소용 대용량 2kg)", "병", 7200, [2, 3], 1),
        ("식용유 18L (콩기름 업소용 캔)", "통", 42000, [2, 3], 1),
        ("마요네즈 (업소용 대용량 3.2kg)", "봉", 11500, [2, 4], 1),
        ("돈가스소스 (업소용 대용량 2kg)", "병", 8300, [2, 3], 1),
        ("쿠시카츠 꼬치대 (대나무 18cm 1000입)", "봉", 5200, [3, 5], 1)]),
    "산들주류상사": ("산들주류상사", "윤산들", [
        ("생맥주 20L", "통", 68000, [2, 3, 4], 1), ("소주 (20병입)", "박스", 38000, [3, 5], 1),
        ("병맥주 (20병입)", "박스", 41000, [2, 3], 1), ("하이볼용 토닉 (24캔입)", "박스", 26000, [2, 3], 1),
        ("청하 (20병입)", "박스", 43000, [1, 2], 1)]),
}

DATE_FMT = ["%Y-%m-%d", "%Y.%m.%d", "%Y년 %m월 %d일", "%y/%m/%d"]


def rand_date():
    d0 = date(2026, 9, 1)
    return d0 + timedelta(days=random.randint(0, 75))


def make_items(sup, n, decimal=False):
    pool = random.sample(S[sup][2], min(n, len(S[sup][2])))
    items = []
    for name, unit, price, qs, tax in pool:
        q = random.choice(qs)
        if decimal and unit == "kg" and random.random() < 0.5:
            q = q + 0.5
        items.append({"item": name, "qty": q, "unit": unit, "unit_price": price,
                      "amount": round(q * price), "taxable": tax})
    return items


def fmt_q(q):
    return str(int(q)) if float(q).is_integer() else f"{q:.1f}"


def won(n):
    return f"{n:,}"


# ---- 문서 정의 ----
docs = []
easy_plan = ["한울닭유통"] * 5 + ["초록마을농산"] * 3 + ["바다한상수산"] * 2 + ["정육명가"] * 2 + \
            ["면가원식품"] * 2 + ["산들주류상사"] * 2
random.shuffle(easy_plan)
tmpl_cycle = ["A", "B"]
for i, sup in enumerate(easy_plan):
    n = random.randint(3, min(9, len(S[sup][2])))
    items = make_items(sup, n, decimal=(i % 3 == 0))
    docs.append({"no": i + 1, "supplier": sup, "date": rand_date(), "items": items,
                 "tmpl": tmpl_cycle[i % 2], "fmt": DATE_FMT[i % 4], "difficulty": "쉬움",
                 "fontname": ["malgun", "batang", "gulim"][i % 3], "notes": ""})

# 어려운 4장
d17 = {"no": 17, "supplier": "한울닭유통", "date": rand_date(), "tmpl": "B", "fmt": "%Y.%m.%d",
       "difficulty": "어려움", "fontname": "batang",
       "items": make_items("한울닭유통", 7)}
d17["correction"] = 2
d17["printed_qty"] = d17["items"][2]["qty"]
d17["items"][2]["qty"] = d17["printed_qty"] + 3  # 손글씨로 정정된 수량(정답)
d17["items"][2]["amount"] = round(d17["items"][2]["qty"] * d17["items"][2]["unit_price"])
d17["notes"] = "손글씨 정정: 3번째 품목 수량·금액·합계를 줄 긋고 다시 적음. 정답은 정정된 값"
docs.append(d17)

d18 = {"no": 18, "supplier": "초록마을농산", "date": rand_date(), "tmpl": "A", "fmt": "%y/%m/%d",
       "difficulty": "어려움", "fontname": "gulim", "items": make_items("초록마을농산", 8, decimal=True),
       "notes": "저화질: 기울어짐 약 3도, 흐림, 노이즈, 낮은 대비"}
docs.append(d18)

d19 = {"no": 19, "supplier": "면가원식품", "date": rand_date(), "tmpl": "C", "fmt": "%Y년 %m월 %d일",
       "difficulty": "어려움", "fontname": "malgun", "items": make_items("면가원식품", 11),
       "notes": "복잡 양식: 규격 칸, 품목명 줄바꿈, 공급가액과 세액 분리, 접힌 자국. 단가·금액은 공급가액 기준, 합계는 세액 포함 최종 합계"}
docs.append(d19)

d20 = {"no": 20, "supplier": "정육명가", "date": rand_date(), "tmpl": "A", "fmt": "%Y.%m.%d",
       "difficulty": "어려움", "fontname": "batang", "items": make_items("정육명가", 4),
       "notes": "원본 오류: 인쇄된 합계가 품목 금액 합과 30,000원 다름. 정답 합계는 인쇄된 값, 합계 불일치로 표시되어야 함. 마지막 줄 '단골 할인'은 음수 금액"}
d20["items"].append({"item": "단골 할인", "qty": 1, "unit": "식", "unit_price": -20000,
                     "amount": -20000, "taxable": 0})
docs.append(d20)

# 합계 계산
for d in docs:
    s = sum(x["amount"] for x in d["items"])
    d["sum_items"] = s
    if d["tmpl"] == "C":
        tax = sum(round(x["amount"] * 0.1) for x in d["items"] if x["taxable"])
        d["tax_total"] = tax
        d["total"] = s + tax
    else:
        d["total"] = s
d20["total"] = d20["sum_items"] + 30000  # 인쇄 오기
d17["printed_total"] = d17["total"] - 3 * d17["items"][2]["unit_price"]

# ---- 렌더링 ----
BLK = (20, 20, 20)
GRY = (225, 225, 225)


def rtext(dr, x, y, s, f):
    dr.text((x, y), s, font=f, fill=BLK, anchor="ra")


def fake_biznum():
    return f"{random.randint(100,899)}-{random.randint(10,99)}-{random.randint(10000,99999)}"


def draw_stamp(dr, cx, cy, r=42):
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(150, 40, 40), width=4)
    dr.text((cx, cy), "인", font=font("bold", 34), fill=(150, 40, 40), anchor="mm")


def wrap(dr, text, f, maxw):
    lines, cur = [], ""
    for ch in text:
        if dr.textlength(cur + ch, font=f) > maxw and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    lines.append(cur)
    return lines


def render(d):
    img = Image.new("RGB", (W, H), (250, 249, 245))
    dr = ImageDraw.Draw(img)
    fn = d["fontname"]
    f = font(fn, 27)
    fs = font(fn, 23)
    fb = font("bold", 30)
    sup = S[d["supplier"]]
    ds = d["date"].strftime(d["fmt"])
    pos = {}  # 손글씨 정정용 좌표 기록
    t = d["tmpl"]
    items = d["items"]
    if t == "A":
        dr.text((W / 2, 95), "거 래 명 세 표", font=font("bold", 54), fill=BLK, anchor="mm")
        dr.text((W - 80, 60), "(공급받는자 보관용)", font=fs, fill=BLK, anchor="ra")
        dr.text((80, 170), f"작성일자 : {ds}", font=f, fill=BLK)
        dr.rectangle((80, 220, W - 80, 380), outline=BLK, width=3)
        dr.line((W / 2, 220, W / 2, 380), fill=BLK, width=2)
        dr.text((100, 235), "공급받는자", font=fs, fill=BLK)
        dr.text((100, 285), "이자카야 샘플점 귀하", font=f, fill=BLK)
        dr.text((W / 2 + 20, 235), f"공급자  상호 : {sup[0]}", font=fs, fill=BLK)
        dr.text((W / 2 + 20, 280), f"대표 : {sup[1]}", font=fs, fill=BLK)
        dr.text((W / 2 + 20, 325), f"등록번호 : {fake_biznum()}", font=fs, fill=BLK)
        cols = [80, 150, 590, 690, 770, 960, W - 80]
        heads = ["번호", "품 목", "수량", "단위", "단 가", "금 액"]
        y0, rh = 420, 54
        dr.rectangle((80, y0, W - 80, y0 + rh), fill=GRY, outline=BLK, width=2)
        for c, h in zip(range(6), heads):
            dr.text(((cols[c] + cols[c + 1]) / 2, y0 + rh / 2), h, font=fs, fill=BLK, anchor="mm")
        y = y0 + rh
        for i, it in enumerate(items):
            dr.rectangle((80, y, W - 80, y + rh), outline=BLK, width=1)
            dr.text(((cols[0] + cols[1]) / 2, y + rh / 2), str(i + 1), font=f, fill=BLK, anchor="mm")
            dr.text((cols[1] + 12, y + 10), it["item"], font=f, fill=BLK)
            pq = d.get("printed_qty") if d.get("correction") == i else it["qty"]
            rtext(dr, cols[3] - 14, y + 10, fmt_q(pq), f)
            dr.text(((cols[3] + cols[4]) / 2, y + rh / 2), it["unit"], font=fs, fill=BLK, anchor="mm")
            rtext(dr, cols[5] - 14, y + 10, won(it["unit_price"]), f)
            rtext(dr, cols[6] - 14, y + 10, won(it["amount"]), f)
            for cx in cols[1:-1]:
                dr.line((cx, y, cx, y + rh), fill=BLK, width=1)
            y += rh
        dr.rectangle((80, y, W - 80, y + 64), outline=BLK, width=3)
        dr.text((110, y + 16), "합 계 금 액", font=fb, fill=BLK)
        rtext(dr, W - 100, y + 14, "₩ " + won(d["total"]), font("bold", 34))
        draw_stamp(dr, W - 160, y + 150)
        dr.text((W - 330, y + 135), "인수자", font=fs, fill=BLK, anchor="ra")
    elif t == "B":
        dr.text((W / 2, 90), sup[0], font=font("bold", 50), fill=BLK, anchor="mm")
        dr.text((W / 2, 150), "납   품   서", font=font(fn, 34), fill=BLK, anchor="mm")
        dr.text((80, 215), f"일자 : {ds}", font=f, fill=BLK)
        dr.text((W - 80, 215), "TEL 02-" + f"{random.randint(300,999)}-{random.randint(1000,9999)}",
                font=fs, fill=BLK, anchor="ra")
        cols = [80, 600, 800, 1000, W - 80]
        heads = ["품   명", "수   량", "단   가", "금   액"]
        y0, rh = 280, 56
        dr.line((80, y0, W - 80, y0), fill=BLK, width=3)
        for c, h in enumerate(heads):
            dr.text(((cols[c] + cols[c + 1]) / 2, y0 + rh / 2), h, font=fs, fill=BLK, anchor="mm")
        y = y0 + rh
        dr.line((80, y, W - 80, y), fill=BLK, width=2)
        for i, it in enumerate(items):
            dr.text((cols[0] + 10, y + 12), it["item"], font=f, fill=BLK)
            pq = d.get("printed_qty") if d.get("correction") == i else it["qty"]
            qx = cols[2] - 20
            rtext(dr, qx, y + 12, f"{fmt_q(pq)}{it['unit']}", f)
            rtext(dr, cols[3] - 20, y + 12, won(it["unit_price"]), f)
            # 정정 줄은 정정 전 금액을 인쇄한다(2026-11-18 수정: 이전에는 정정 후 금액이 인쇄됐음.
            # sample-invoices·dev-invoices의 17번 PDF는 수정 전 코드로 만들어졌다)
            pa = round(pq * it["unit_price"]) if d.get("correction") == i else it["amount"]
            rtext(dr, cols[4] - 20, y + 12, won(pa), f)
            if d.get("correction") == i:
                pos["qty"] = (qx, y + 12, len(f"{fmt_q(pq)}{it['unit']}"))
                pos["amt"] = (cols[4] - 20, y + 12)
            y += rh
            dr.line((80, y, W - 80, y), fill=(120, 120, 120), width=1)
        dr.line((80, y + 4, W - 80, y + 4), fill=BLK, width=3)
        dr.text((cols[0] + 10, y + 24), "합  계", font=fb, fill=BLK)
        tot = d.get("printed_total", d["total"])
        rtext(dr, cols[4] - 20, y + 22, won(tot), font("bold", 32))
        pos["tot"] = (cols[4] - 20, y + 22)
        dr.text((80, y + 110), "※ 상기 물품을 정히 납품합니다.", font=fs, fill=BLK)
    else:  # C
        dr.text((W / 2, 85), "거래명세서", font=font("bold", 52), fill=BLK, anchor="mm")
        dr.text((80, 150), f"일자 {ds}", font=f, fill=BLK)
        dr.rectangle((80, 200, W - 80, 340), outline=BLK, width=3)
        dr.line((W / 2, 200, W / 2, 340), fill=BLK, width=2)
        dr.text((100, 215), "받는분", font=fs, fill=BLK)
        dr.text((100, 265), "이자카야 샘플점", font=f, fill=BLK)
        dr.text((W / 2 + 20, 215), f"공급자 : {sup[0]}", font=fs, fill=BLK)
        dr.text((W / 2 + 20, 260), f"대표자 : {sup[1]}", font=fs, fill=BLK)
        dr.text((W / 2 + 20, 300), f"사업자번호 {fake_biznum()}", font=fs, fill=BLK)
        cols = [80, 130, 520, 700, 780, 900, 1060, W - 80]
        heads = ["No", "품목", "규격", "수량", "단가", "공급가액", "세액"]
        y0, rh = 380, 50
        dr.rectangle((80, y0, W - 80, y0 + rh), fill=GRY, outline=BLK, width=2)
        for c, h in enumerate(heads):
            dr.text(((cols[c] + cols[c + 1]) / 2, y0 + rh / 2), h, font=fs, fill=BLK, anchor="mm")
        y = y0 + rh
        fsm = font(fn, 24)
        for i, it in enumerate(items):
            name = it["item"]
            lines = wrap(dr, name, fsm, cols[2] - cols[1] - 20)
            h = max(rh, 16 + 30 * len(lines))
            dr.rectangle((80, y, W - 80, y + h), outline=BLK, width=1)
            dr.text(((cols[0] + cols[1]) / 2, y + 22), str(i + 1), font=fsm, fill=BLK, anchor="mm")
            for k, ln in enumerate(lines):
                dr.text((cols[1] + 10, y + 8 + 30 * k), ln, font=fsm, fill=BLK)
            dr.text(((cols[2] + cols[3]) / 2, y + 8), "EA" if it["unit"] != "kg" else "kg",
                    font=fsm, fill=BLK, anchor="ma")
            rtext(dr, cols[4] - 10, y + 8, fmt_q(it["qty"]), fsm)
            rtext(dr, cols[5] - 10, y + 8, won(it["unit_price"]), fsm)
            rtext(dr, cols[6] - 10, y + 8, won(it["amount"]), fsm)
            tx = round(it["amount"] * 0.1) if it["taxable"] else 0
            rtext(dr, cols[7] - 10, y + 8, won(tx) if it["taxable"] else "면세", fsm)
            for cx in cols[1:-1]:
                dr.line((cx, y, cx, y + h), fill=BLK, width=1)
            y += h
        y += 20
        dr.rectangle((80, y, W - 80, y + 150), outline=BLK, width=3)
        dr.text((110, y + 14), "공급가액 계", font=fs, fill=BLK)
        rtext(dr, W - 110, y + 12, won(d["sum_items"]), f)
        dr.text((110, y + 58), "세액 계", font=fs, fill=BLK)
        rtext(dr, W - 110, y + 56, won(d["tax_total"]), f)
        dr.line((80, y + 100, W - 80, y + 100), fill=BLK, width=2)
        dr.text((110, y + 108), "합 계 금 액", font=fb, fill=BLK)
        rtext(dr, W - 110, y + 106, won(d["total"]), font("bold", 34))
        dr.text((80, y + 180), "비고 : 계좌 우리은행 1002-000-000000 " + sup[1], font=fs, fill=BLK)
    return img, pos


def handwrite(img, d, pos):
    dr = ImageDraw.Draw(img)
    i = d["correction"]
    it = d["items"][i]
    h = font("hand", 38)
    qx, qy, nch = pos["qty"]
    w = 24 * nch + 20
    dr.line((qx - w, qy + 18, qx + 4, qy + 14), fill=(30, 30, 90), width=3)
    dr.text((qx - 20, qy - 28), f"{fmt_q(it['qty'])}{it['unit']}", font=h, fill=(30, 30, 90), anchor="ra")
    ax, ay = pos["amt"]
    dr.line((ax - 130, ay + 16, ax + 4, ay + 12), fill=(30, 30, 90), width=3)
    dr.text((ax - 10, ay - 30), won(it["amount"]), font=h, fill=(30, 30, 90), anchor="ra")
    tx, ty = pos["tot"]
    dr.line((tx - 150, ty + 20, tx + 4, ty + 16), fill=(30, 30, 90), width=3)
    dr.text((tx - 10, ty - 34), won(d["total"]), font=font("hand", 44), fill=(30, 30, 90), anchor="ra")


def scan(img, angle=0.4, noise=4, blur=0.6, shadow=True, fold=None, jpeg=80, down=None, contrast=1.0):
    g = img.convert("L").rotate(angle, resample=Image.BICUBIC, fillcolor=232)
    a = np.asarray(g, dtype=np.float32)
    if contrast != 1.0:
        a = (a - 128) * contrast + 128 + (1 - contrast) * 40
    hh, ww = a.shape
    if shadow:
        xs = np.arange(ww)
        a *= (1 - 0.12 * np.exp(-xs / 70.0))[None, :]
    if fold:
        ys = np.arange(hh)[:, None]
        a -= 55 * np.exp(-((ys - fold) / 3.0) ** 2)
        a *= 1 - 0.10 * np.exp(-((ys - fold) / 70.0) ** 2)
        a += 12 * np.exp(-((ys - fold - 14) / 6.0) ** 2)
    a += np.random.normal(0, noise, a.shape)
    g = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    if blur:
        g = g.filter(ImageFilter.GaussianBlur(blur))
    if down:
        g = g.resize((int(ww * down), int(hh * down)), Image.BILINEAR).resize((ww, hh), Image.BILINEAR)
    buf = io.BytesIO()
    g.save(buf, "JPEG", quality=jpeg)
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


rows, answers = [], []
for d in docs:
    img, pos = render(d)
    if d.get("correction") is not None:
        handwrite(img, d, pos)
    if CLEAN_DIR:
        img.save(os.path.join(CLEAN_DIR, f"명세서_{d['no']:02d}.png"))
    if d["no"] == 18:
        out = scan(img, angle=-3.0, noise=14, blur=1.3, jpeg=45, down=0.55, contrast=0.72)
    elif d["no"] == 19:
        out = scan(img, angle=0.8, noise=6, blur=0.8, fold=int(H * 0.46), jpeg=70)
    elif d["no"] == 17:
        out = scan(img, angle=0.6, noise=6, blur=0.8, jpeg=75)
    elif d["no"] == 20:
        out = scan(img, angle=-0.7, noise=6, blur=0.8, jpeg=75)
    else:
        out = scan(img, angle=random.uniform(-0.8, 0.8), noise=random.uniform(3, 6),
                   blur=random.uniform(0.4, 0.8), jpeg=random.randint(70, 85))
    fname = f"명세서_{d['no']:02d}.pdf"
    out.save(os.path.join(OUT, fname), "PDF", resolution=150)
    if PREVIEW:
        out.resize((W // 2, H // 2)).save(os.path.join(PREVIEW, f"{d['no']:02d}.png"))
    entry = {"file": fname, "supplier": d["supplier"], "date": d["date"].isoformat(),
             "difficulty": d["difficulty"], "doc_total": d["total"],
             "sum_of_items": d["sum_items"], "total_mismatch": d["total"] != d["sum_items"] + d.get("tax_total", 0),
             "notes": d["notes"], "items": [{k: x[k] for k in ("item", "qty", "unit", "unit_price", "amount")}
                                            for x in d["items"]]}
    answers.append(entry)
    for x in d["items"]:
        rows.append([fname, d["supplier"], d["date"].isoformat(), x["item"], x["qty"], x["unit"],
                     x["unit_price"], x["amount"], d["total"], d["difficulty"], d["notes"]])

with open(os.path.join(OUT, "정답.json"), "w", encoding="utf-8") as fp:
    json.dump(answers, fp, ensure_ascii=False, indent=2)
with open(os.path.join(OUT, "정답_품목.csv"), "w", encoding="utf-8-sig", newline="") as fp:
    w = csv.writer(fp)
    w.writerow(["파일", "거래처", "날짜", "품목", "수량", "단위", "단가", "금액", "문서합계", "난이도", "비고"])
    w.writerows(rows)
print(len(docs), "docs,", len(rows), "items")
