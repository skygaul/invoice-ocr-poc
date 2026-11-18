"""후보 A 재현 확인: 시험용 20장에 A를 돌려 채점하고, 저장소에 들어 있는 기대 결과와 비교한다.

사용(프로젝트 폴더에서, 모델 준비 후): python -X utf8 -W ignore src/reproduce.py
- 통과 조건: 기준 값(오류 개수, 확인 필요 비율, 합계 불일치 표시, 위치 추적)이 기대 결과와 같다.
- 처리 시간은 컴퓨터마다 달라서 비교하지 않고 60초 이하인지만 본다.
- 예측 값 전체도 기대 결과와 비교해 다르면 알려 준다(참고 정보. 글자 인식이 컴퓨터마다 미세하게 다를 수 있음).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout + p.stderr


def strip_seconds(preds):
    return [{k: v for k, v in d.items() if k != "seconds"} for d in preds]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default="sample-invoices")
    ap.add_argument("--gt", default="sample-invoices/정답.json")
    ap.add_argument("--expected-dir", default="expected")
    ap.add_argument("--use-run", default="", help="이미 있는 실행 폴더(outputs/A/...)를 쓰고 A를 다시 돌리지 않음")
    a = ap.parse_args()

    py = sys.executable
    if a.use_run:
        run_dir = a.use_run
    else:
        print("후보 A를 실행합니다(약 6분, CPU 사양에 따라 더 걸림)...", flush=True)
        code, out = run([py, "-X", "utf8", "-W", "ignore", "src/run_a.py", "--pdf-dir", a.pdf_dir])
        m = re.search(r"저장:\s*(.+)", out)
        if code != 0 or not m:
            print(out[-3000:])
            sys.exit("A 실행이 실패했습니다. 모델을 먼저 받았는지(python src/prepare_a.py) 확인하세요.")
        run_dir = m.group(1).strip()

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join("results", f"repro_{stamp}")
    code, out = run([py, "-X", "utf8", "src/score.py", "--pred", os.path.join(run_dir, "predictions.json"),
                     "--gt", a.gt, "--out", out_dir])
    if code != 0:
        print(out[-3000:])
        sys.exit("채점이 실패했습니다.")

    with open(os.path.join(ROOT, out_dir, "summary.json"), encoding="utf-8") as f:
        got = json.load(f)
    with open(os.path.join(ROOT, a.expected_dir, "A_test_summary.json"), encoding="utf-8") as f:
        exp = json.load(f)

    problems = []
    for k, c in exp["criteria"].items():
        if k.startswith("6_"):
            continue
        g = got["criteria"][k]["값"]
        e = c["값"]
        same = abs(g - e) < 1e-9 if isinstance(e, float) and isinstance(g, (int, float)) else g == e
        print(f"{k}: 기대 {e} / 실제 {g} {'일치' if same else '불일치'}")
        if not same:
            problems.append(k)
    for key in ("참고", "난이도별"):
        if got[key] != exp[key]:
            problems.append(key)
            print(f"{key}: 기대와 다름")
    t = got["criteria"]["6_처리시간"]
    print(f"6_처리시간: 평균 {t['값']}초 (기대 환경 {exp['criteria']['6_처리시간']['값']}초, 기준 60초 이하) "
          f"{'통과' if t['통과'] else '미달'}")
    if not t["통과"]:
        problems.append("6_처리시간")

    with open(os.path.join(ROOT, a.expected_dir, "A_test_predictions.json"), encoding="utf-8") as f:
        exp_pred = json.load(f)
    with open(os.path.join(ROOT, run_dir, "predictions.json"), encoding="utf-8") as f:
        got_pred = strip_seconds(json.load(f))
    diff = [e["file"] for e, g in zip(exp_pred, got_pred) if e != g]
    print(f"예측 값 전체 비교: {'같음' if not diff else f'{len(diff)}장 다름 ' + ', '.join(diff)} (참고 정보)")

    print("결과 폴더:", out_dir)
    if problems:
        sys.exit("재현 실패: " + ", ".join(problems))
    print("재현 성공: 기준 값이 기대 결과와 같습니다.")


if __name__ == "__main__":
    main()
