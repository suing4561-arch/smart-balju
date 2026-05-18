"""
나이스오케이포스 ASP 매출 자동 수집 스크래퍼 v10
- Firebase okpos_config/ 에서 브랜드별 계정 자동 로드
- 다중 브랜드 지원
- GitHub Actions 자동화 지원
"""

import requests
import json
import datetime
import time
import re
import os
from typing import Optional

# .env 파일 자동 로드 (로컬 실행 시)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FIREBASE_DB_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://smart-balju-default-rtdb.asia-southeast1.firebasedatabase.app"
)
OKPOS_BASE = "https://nice.okpos.co.kr"


# ── Firebase REST API ──────────────────────────────────────
def fb_get(path: str) -> dict:
    url = f"{FIREBASE_DB_URL}/{path}.json"
    resp = requests.get(url, timeout=10)
    if resp.status_code == 200:
        return resp.json() or {}
    return {}

def fb_put(path: str, data: dict) -> bool:
    url = f"{FIREBASE_DB_URL}/{path}.json"
    resp = requests.put(url, json=data, timeout=15)
    return resp.status_code == 200

def fb_patch(path: str, data: dict) -> bool:
    url = f"{FIREBASE_DB_URL}/{path}.json"
    resp = requests.patch(url, json=data, timeout=15)
    return resp.status_code == 200


# ── OKPos 로그인/수집 ──────────────────────────────────────
def get_hidden_fields(html: str) -> dict:
    fields = {}
    for p in [
        r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*type=["\']hidden["\'][^>]*value=["\']([^"\']*)["\']',
        r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*type=["\']hidden["\']',
    ]:
        for name, val in re.findall(p, html, re.IGNORECASE):
            fields[name] = val
    return fields

def get_form_action(html: str, base_url: str) -> Optional[str]:
    m = re.search(r'<form[^>]*action=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m: return None
    action = m.group(1)
    if action.startswith("http"): return action
    if action.startswith("/"): return base_url + action
    return base_url + "/login/" + action

def login(session: requests.Session, uid: str, upw: str) -> bool:
    base = OKPOS_BASE
    hdr = lambda ref: {"Referer": ref, "Origin": base, "Content-Type": "application/x-www-form-urlencoded"}

    r1 = session.get(f"{base}/login/login_form.jsp", timeout=15)
    h1 = get_hidden_fields(r1.text)
    print(f"  [S1] {r1.status_code}")

    r2 = session.post(f"{base}/login/login_check.jsp",
                      data={"user_id": uid, "user_pwd": upw, "id_chk": "", "auto_login_chk": "", **h1},
                      headers=hdr(f"{base}/login/login_form.jsp"), timeout=15, allow_redirects=True)
    h2 = get_hidden_fields(r2.text)
    a3 = get_form_action(r2.text, base) or f"{base}/login/login_check_action.jsp"
    print(f"  [S2] {r2.status_code}")

    r3 = session.post(a3, data={"user_id": uid, "user_pwd": upw, **h1, **h2},
                      headers=hdr(r2.url), timeout=15, allow_redirects=True)
    print(f"  [S3] {r3.status_code}")

    if "error.jsp" in r3.text:
        print("  ❌ 로그인 실패")
        return False

    a4 = get_form_action(r3.text, base)
    h3 = get_hidden_fields(r3.text)
    if a4 and a4 != a3 and "error" not in a4:
        session.post(a4, data={"user_id": uid, "user_pwd": upw, **h3},
                     headers=hdr(r3.url), timeout=15, allow_redirects=True)

    time.sleep(1)
    chk = session.get(f"{base}/login/top_frame.jsp", timeout=15)
    if "로그아웃" in chk.text or "divTopFrameHead" in chk.text:
        print("  ✅ 로그인 성공!")
        return True
    print("  ⚠️ 로그인 불명확 — 계속 진행")
    return True

def fetch_sales(session: requests.Session, date_from: str, date_to: str) -> list:
    base = OKPOS_BASE
    r_page = session.get(f"{base}/sale/day/day_jump010.jsp", timeout=15)
    print(f"  [매출페이지] {r_page.status_code}, {len(r_page.content):,} bytes")

    token_key, token_val = "", ""
    for k, v in get_hidden_fields(r_page.text).items():
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', v, re.IGNORECASE):
            token_key, token_val = k, v
            break

    time.sleep(0.5)
    payload = {
        "S_CONTROLLER": "sale.day.day_total010", "S_METHOD": "search",
        "SHEETSEQ": "1",
        "S_SAVENAME": "SALE_DATE|SHOP_CD|SHOP_NM|TOT_SALE_AMT|TOT_DC_AMT|DCM_SALE_AMT|VAT_AMT|TOT_SALE_CNT|FD_GST_CNT_T|SALE_PER_GST|TABLE_CNT|CASH_AMT2|CASH_BILL_AMT|CRD_CARD_AMT|O2O_AMT",
        "S_ORDERBY": "", "date1_1": date_from, "date1_2": date_to,
        "date_period1": "366", "ss_SHOP_CD": "", "ss_SHOP_NM": "전체", "ss_SHOP_INFO": "[]",
    }
    if token_key: payload[token_key] = token_val

    resp = session.post(f"{base}/sale/day/ddd.htmlSheetAction", data=payload,
                        headers={"Referer": f"{base}/sale/day/day_jump010.jsp", "Origin": base,
                                 "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    print(f"  [매출 조회] {resp.status_code}, {len(resp.content):,} bytes")

    try:
        data = json.loads(resp.text)
        rows = data.get("Data", [])
        if not rows:
            print(f"  ⚠️ 데이터 없음: {resp.text[:200]}")
            return []
    except Exception as e:
        print(f"  ❌ 파싱 오류: {e}")
        return []

    results = []
    for row in rows:
        shop_cd = row.get("SHOP_CD", "")
        if not shop_cd: continue
        def to_int(v):
            try: return int(float(str(v).replace(",", "")))
            except: return 0
        results.append({
            "SHOP_CD":      shop_cd,
            "SHOP_NM":      row.get("SHOP_NM", ""),
            "SALE_DATE":    row.get("SALE_DATE", date_from),
            "TOT_SALE_AMT": to_int(row.get("TOT_SALE_AMT", 0)),
            "DCM_SALE_AMT": to_int(row.get("DCM_SALE_AMT", 0)),
            "TOT_SALE_CNT": to_int(row.get("TOT_SALE_CNT", 0)),
            "FD_GST_CNT_T": to_int(row.get("FD_GST_CNT_T", 0)),
            "SALE_PER_GST": to_int(row.get("SALE_PER_GST", 0)),
            "CRD_CARD_AMT": to_int(row.get("CRD_CARD_AMT", 0)),
            "CASH_AMT2":    to_int(row.get("CASH_AMT2", 0)),
            "VAT_AMT":      to_int(row.get("VAT_AMT", 0)),
        })
    print(f"  [파싱 완료] {len(results)}개 가맹점")
    return results


# ── 메인 ──────────────────────────────────────────────────
def collect_for_brand(brand_id: str, cfg: dict, date_from: str, date_to: str):
    """단일 브랜드 매출 수집"""
    brand_name = cfg.get("brandName", brand_id)
    okpos_id   = cfg.get("okposId", "")
    okpos_pw   = cfg.get("okposPw", "")

    print(f"\n{'─'*50}")
    print(f"  브랜드: {brand_name} ({okpos_id})")
    print(f"  조회: {date_from} ~ {date_to}")
    print(f"{'─'*50}")

    if not okpos_id or not okpos_pw:
        print("  ❌ OKPos 계정 정보 없음 — 건너뜀")
        return False

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })

    if not login(session, okpos_id, okpos_pw):
        return False

    time.sleep(1)
    sales = fetch_sales(session, date_from, date_to)

    if not sales:
        print("  ❌ 매출 데이터 없음")
        return False

    # 결과 출력
    total = sum(r.get("TOT_SALE_AMT", 0) for r in sales)
    print(f"\n  {'매장명':<20} {'총매출':>12} {'주문':>6}")
    print(f"  {'-'*42}")
    for row in sales:
        print(f"  {row.get('SHOP_NM',''):<20} {row.get('TOT_SALE_AMT',0):>12,} {row.get('TOT_SALE_CNT',0):>6,}")
    print(f"  {'합계':<20} {total:>12,}")

    # Firebase 저장 - okpos_sales/{date}/{brand_id}/{shop_cd}
    date_key = date_from.replace('-', '')
    output = {}
    for row in sales:
        shop_cd = row.get("SHOP_CD", "")
        if shop_cd:
            output[shop_cd] = {
                "shop_nm":      row.get("SHOP_NM", ""),
                "brand_id":     brand_id,
                "brand_name":   brand_name,
                "date":         date_from,
                "tot_sale_amt": row.get("TOT_SALE_AMT", 0),
                "dcm_sale_amt": row.get("DCM_SALE_AMT", 0),
                "tot_sale_cnt": row.get("TOT_SALE_CNT", 0),
                "gst_cnt":      row.get("FD_GST_CNT_T", 0),
                "sale_per_gst": row.get("SALE_PER_GST", 0),
                "card_amt":     row.get("CRD_CARD_AMT", 0),
                "cash_amt":     row.get("CASH_AMT2", 0),
                "updated_at":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

    # 브랜드별 경로에 저장: okpos_sales/20260517/brandId/
    path = f"okpos_sales/{date_key}/{brand_id}"
    if fb_put(path, output):
        print(f"\n  ✅ Firebase 저장 완료! → okpos_sales/{date_key}/{brand_id}")
        # 마지막 수집 시간 업데이트
        fb_patch(f"okpos_config/{brand_id}", {
            "lastCollected": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        return True
    else:
        # 구버전 호환: 브랜드 구분 없이 저장
        if fb_put(f"okpos_sales/{date_key}", output):
            print(f"\n  ✅ Firebase 저장 완료! (구버전 경로)")
            return True
        print(f"\n  ❌ Firebase 저장 실패")
        return False


def main(date_from=None, date_to=None):
    if not date_from:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        date_from = date_to = yesterday.strftime("%Y-%m-%d")
    if not date_to:
        date_to = date_from

    print(f"\n{'='*50}")
    print(f"  나이스오케이포스 매출 수집 v10")
    print(f"  조회: {date_from} ~ {date_to}")
    print(f"{'='*50}")

    # 1. Firebase에서 브랜드별 OKPos 계정 로드
    configs = fb_get("okpos_config")

    # 2. 환경변수 폴백 (Firebase에 설정 없을 때)
    env_id = os.environ.get("OKPOS_ID", "")
    env_pw = os.environ.get("OKPOS_PW", "")

    if not configs and env_id and env_pw:
        print("\n환경변수에서 계정 로드 (Firebase 설정 없음)")
        configs = {"_env": {"okposId": env_id, "okposPw": env_pw, "brandName": "기본 브랜드"}}

    if not configs:
        print("\n❌ OKPos 계정이 없습니다.")
        print("   site_hq.html → 설정 탭에서 OKPos 계정을 등록하거나")
        print("   GitHub Secrets에 OKPOS_ID / OKPOS_PW 를 설정하세요.")
        return

    print(f"\n  등록된 브랜드: {len(configs)}개")

    # 3. 브랜드별 수집
    success, fail = 0, 0
    for brand_id, cfg in configs.items():
        try:
            if collect_for_brand(brand_id, cfg, date_from, date_to):
                success += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  ❌ 오류: {e}")
            fail += 1
        time.sleep(2)  # 브랜드 간 딜레이

    print(f"\n{'='*50}")
    print(f"  완료! 성공 {success}개 / 실패 {fail}개")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "schedule":
        import schedule
        def job():
            d = (datetime.date.today()-datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            main(d, d)
        schedule.every().day.at("02:00").do(job)
        print("매일 02:00 자동 수집 시작")
        while True:
            schedule.run_pending()
            time.sleep(60)
    elif len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] != "schedule":
        main(sys.argv[1], sys.argv[1])
    else:
        main()
