"""
나이스오케이포스 ASP 매출 자동 수집 스크래퍼 v8
- JSON 응답 파싱
- 토큰 자동 추출
"""

import requests
import json
import datetime
import time
import re
from typing import Optional

CONFIG = {
    "id":       "여기에_OKPos_아이디",
    "pw":       "여기에_OKPos_비밀번호",
    "base_url": "https://nice.okpos.co.kr",
}

FIREBASE_ENABLED = False
# FIREBASE_CREDENTIAL_PATH = "firebase-credentials.json"
# FIREBASE_DB_URL = "https://your-project.firebaseio.com"


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
    if not m:
        return None
    action = m.group(1)
    if action.startswith("http"):   return action
    if action.startswith("/"):      return base_url + action
    return base_url + "/login/" + action


def login(session: requests.Session) -> bool:
    base = CONFIG["base_url"]
    uid  = CONFIG["id"]
    upw  = CONFIG["pw"]
    hdr  = lambda ref: {
        "Referer": ref, "Origin": base,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r1 = session.get(f"{base}/login/login_form.jsp", timeout=15)
    h1 = get_hidden_fields(r1.text)
    print(f"[S1] {r1.status_code}")

    r2 = session.post(f"{base}/login/login_check.jsp",
                      data={"user_id": uid, "user_pwd": upw,
                            "id_chk": "", "auto_login_chk": "", **h1},
                      headers=hdr(f"{base}/login/login_form.jsp"),
                      timeout=15, allow_redirects=True)
    h2 = get_hidden_fields(r2.text)
    a3 = get_form_action(r2.text, base) or f"{base}/login/login_check_action.jsp"
    print(f"[S2] {r2.status_code} → {a3.split('/')[-1]}")

    r3 = session.post(a3,
                      data={"user_id": uid, "user_pwd": upw, **h1, **h2},
                      headers=hdr(r2.url),
                      timeout=15, allow_redirects=True)
    print(f"[S3] {r3.status_code}")

    if "error.jsp" in r3.text:
        print("❌ 로그인 실패 — 아이디/비밀번호 확인")
        return False

    a4 = get_form_action(r3.text, base)
    h3 = get_hidden_fields(r3.text)
    if a4 and a4 != a3 and "error" not in a4:
        r4 = session.post(a4, data={"user_id": uid, "user_pwd": upw, **h3},
                          headers=hdr(r3.url), timeout=15, allow_redirects=True)
        print(f"[S4] {r4.status_code}")

    time.sleep(1)
    chk = session.get(f"{base}/login/top_frame.jsp", timeout=15)
    if "로그아웃" in chk.text or "divTopFrameHead" in chk.text:
        print("✅ 로그인 성공!")
        return True
    print("⚠️ 로그인 불명확 — 계속 진행")
    return True


def fetch_sales(session: requests.Session, date_from: str,
                date_to: str, shop_cd: str = "") -> Optional[list]:
    base = CONFIG["base_url"]

    # 매출 페이지 접속 → 토큰 추출
    r_page = session.get(f"{base}/sale/day/day_jump010.jsp", timeout=15)
    print(f"[매출페이지] {r_page.status_code}, {len(r_page.content):,} bytes")

    token_key, token_val = "", ""
    hidden = get_hidden_fields(r_page.text)
    for k, v in hidden.items():
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', v, re.IGNORECASE):
            token_key, token_val = k, v
            print(f"[토큰] {k[:16]}... = {v[:16]}...")
            break

    time.sleep(0.5)

    payload = {
        "S_CONTROLLER": "sale.day.day_total010",
        "S_METHOD":     "search",
        "SHEETSEQ":     "1",
        "S_SAVENAME": (
            "SALE_DATE|SALE_YOIL|SHOP_CD|SHOP_NM|"
            "TOT_SALE_AMT|TOT_DC_AMT|DCM_SALE_AMT|"
            "NO_TAX_SALE_AMT|VAT_AMT|TOT_SALE_CNT|"
            "DCM_TOT_RATE|FD_GST_CNT_T|SALE_PER_GST|"
            "FD_GST_CNT_1|FD_GST_CNT_2|FD_GST_CNT_3|FD_GST_CNT_4|"
            "TABLE_CNT|SALE_PER_TABLE|GST_PER_TABLE|"
            "SVC_TIP_AMT|TOT_ETC_AMT|TOT_PAY_AMT|"
            "CASH_AMT2|CASH_BILL_AMT|CRD_CARD_AMT|"
            "WES_AMT|TK_GFT_AMT|TK_FOD_AMT|CST_POINT_AMT|"
            "JCD_CARD_AMT|KP_AMT|"
            "P01_AMT|P02_AMT|P03_AMT|P04_AMT|P05_AMT|"
            "P06_AMT|P07_AMT|P08_AMT|P09_AMT|P10_AMT|"
            "P11_AMT|P12_AMT|P13_AMT|P14_AMT|P15_AMT|"
            "P16_AMT|P17_AMT|P18_AMT|P19_AMT|P20_AMT|"
            "P21_AMT|P22_AMT|P23_AMT|P24_AMT|P25_AMT|"
            "P26_AMT|P27_AMT|P28_AMT|P29_AMT|P30_AMT|"
            "P99_AMT|RFC_AMT|MCP_AMT|PCD_CARD_AMT|EGIFT_AMT|"
            "O2O_AMT|ETC_PAY_AMT|"
            "GEN_DCM_SALE_AMT|GEN_DCM_SALE_RATE|"
            "PKG_DCM_SALE_AMT|PKG_DCM_SALE_RATE|"
            "DLV_DCM_SALE_AMT|DLV_DCM_SALE_RATE|"
            "DC_GEN_AMT|DC_SVC_AMT|DC_PCD_AMT|DC_CPN_AMT|"
            "DC_CST_AMT|DC_TFD_AMT|DC_PACK_AMT|DC_YAP_AMT|"
            "A_TAX_RFND_AMT|D_TAX_RFND_AMT|D_TAX_RFND_FEE"
        ),
        "S_ORDERBY":    "",
        "date1_1":      date_from,
        "date1_2":      date_to,
        "date_period1": "366",
        "ss_SHOP_CD":   shop_cd,
        "ss_SHOP_NM":   "" if shop_cd else "전체",
        "ss_SHOP_INFO": "[]",
    }
    if token_key:
        payload[token_key] = token_val

    resp = session.post(
        f"{base}/sale/day/ddd.htmlSheetAction",
        data=payload,
        headers={
            "Referer":      f"{base}/sale/day/day_jump010.jsp",
            "Origin":       base,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30
    )
    print(f"[매출 조회] {resp.status_code}, {len(resp.content):,} bytes")

    if resp.status_code != 200:
        return None

    # ── JSON 파싱 ──
    return parse_json(resp.text, date_from)


def parse_json(raw: str, date: str) -> list:
    """JSON 형식 응답 파싱"""
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[파싱 오류] JSON 파싱 실패: {e}")
        print(f"[원문] {raw[:300]}")
        return []

    rows = data.get("Data", [])
    if not rows:
        print(f"[파싱] Data 없음. 전체 키: {list(data.keys())}")
        print(f"[원문] {raw[:300]}")
        return []

    results = []
    for row in rows:
        shop_cd = row.get("SHOP_CD", "")
        if not shop_cd:
            continue  # 합계 행 제외

        def to_int(v):
            try:
                return int(float(str(v).replace(",", "")))
            except:
                return 0

        results.append({
            "SALE_DATE":    row.get("SALE_DATE", date),
            "SALE_YOIL":    row.get("SALE_YOIL", ""),
            "SHOP_CD":      shop_cd,
            "SHOP_NM":      row.get("SHOP_NM", ""),
            "TOT_SALE_AMT": to_int(row.get("TOT_SALE_AMT", 0)),
            "TOT_DC_AMT":   to_int(row.get("TOT_DC_AMT", 0)),
            "DCM_SALE_AMT": to_int(row.get("DCM_SALE_AMT", 0)),
            "TOT_SALE_CNT": to_int(row.get("TOT_SALE_CNT", 0)),
            "FD_GST_CNT_T": to_int(row.get("FD_GST_CNT_T", 0)),
            "SALE_PER_GST": to_int(row.get("SALE_PER_GST", 0)),
            "CRD_CARD_AMT": to_int(row.get("CRD_CARD_AMT", 0)),
            "CASH_AMT2":    to_int(row.get("CASH_AMT2", 0)),
            "CASH_BILL_AMT":to_int(row.get("CASH_BILL_AMT", 0)),
            "VAT_AMT":      to_int(row.get("VAT_AMT", 0)),
            "TABLE_CNT":    to_int(row.get("TABLE_CNT", 0)),
        })

    print(f"[파싱 완료] {len(results)}개 가맹점")
    return results


def save_to_json(sales_data: list, date: str):
    filename = f"okpos_sales_{date.replace('-','')}.json"
    output = {}
    for row in sales_data:
        shop_cd = row.get("SHOP_CD", "")
        if shop_cd:
            output[shop_cd] = {
                "shop_nm":      row.get("SHOP_NM", ""),
                "date":         date,
                "tot_sale_amt": row.get("TOT_SALE_AMT", 0),
                "dcm_sale_amt": row.get("DCM_SALE_AMT", 0),
                "tot_sale_cnt": row.get("TOT_SALE_CNT", 0),
                "gst_cnt":      row.get("FD_GST_CNT_T", 0),
                "sale_per_gst": row.get("SALE_PER_GST", 0),
                "card_amt":     row.get("CRD_CARD_AMT", 0),
                "cash_amt":     row.get("CASH_AMT2", 0),
                "updated_at":   datetime.datetime.now().strftime("%H:%M"),
            }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ JSON 저장: {filename} ({len(output)}개 가맹점)")
    return filename


def save_to_firebase(sales_data: list, date: str):
    try:
        import firebase_admin
        from firebase_admin import credentials, db
        if not firebase_admin._apps:
            cred = credentials.Certificate(FIREBASE_CREDENTIAL_PATH)
            firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})
        ref = db.reference(f"okpos_sales/{date.replace('-','')}")
        output = {}
        for row in sales_data:
            shop_cd = row.get("SHOP_CD", "")
            if shop_cd:
                output[shop_cd] = {
                    "shop_nm":      row.get("SHOP_NM", ""),
                    "date":         date,
                    "tot_sale_amt": row.get("TOT_SALE_AMT", 0),
                    "dcm_sale_amt": row.get("DCM_SALE_AMT", 0),
                    "tot_sale_cnt": row.get("TOT_SALE_CNT", 0),
                    "gst_cnt":      row.get("FD_GST_CNT_T", 0),
                    "sale_per_gst": row.get("SALE_PER_GST", 0),
                    "card_amt":     row.get("CRD_CARD_AMT", 0),
                    "cash_amt":     row.get("CASH_AMT2", 0),
                    "updated_at":   datetime.datetime.now().strftime("%H:%M"),
                }
        ref.set(output)
        print(f"✅ Firebase 저장: {len(output)}개 가맹점")
        return True
    except Exception as e:
        print(f"❌ Firebase 오류: {e}")
        return False


def main(date_from=None, date_to=None):
    if not date_from:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        date_from = date_to = yesterday.strftime("%Y-%m-%d")

    print(f"\n{'='*50}")
    print(f"  나이스오케이포스 매출 수집 v8")
    print(f"  조회: {date_from} ~ {date_to}")
    print(f"  계정: {CONFIG['id']}")
    print(f"{'='*50}\n")

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    })

    if not login(session):
        input("\n아무 키나 누르면 닫힙니다...")
        return

    time.sleep(1)
    sales = fetch_sales(session, date_from, date_to)

    if not sales:
        print("\n❌ 매출 데이터 없음")
        input("\n아무 키나 누르면 닫힙니다...")
        return

    print(f"\n{'='*55}")
    print(f"  📊 수집 결과 ({date_from})")
    print(f"{'='*55}")
    print(f"{'매장명':<20} {'총매출':>12} {'실매출':>12} {'주문':>6} {'고객':>6}")
    print("-" * 60)
    for row in sales:
        print(
            f"{row.get('SHOP_NM',''):<20} "
            f"{row.get('TOT_SALE_AMT',0):>12,} "
            f"{row.get('DCM_SALE_AMT',0):>12,} "
            f"{row.get('TOT_SALE_CNT',0):>6,} "
            f"{row.get('FD_GST_CNT_T',0):>6,}"
        )
    total = sum(r.get("TOT_SALE_AMT", 0) for r in sales)
    print("-" * 60)
    print(f"{'합계':<20} {total:>12,}")
    print(f"{'='*55}")

    if FIREBASE_ENABLED:
        save_to_firebase(sales, date_from)
    else:
        save_to_json(sales, date_from)

    print(f"\n✅ 완료! {len(sales)}개 가맹점 수집")
    input("\n아무 키나 누르면 닫힙니다...")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "schedule":
        import schedule
        def job():
            d = (datetime.date.today()-datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            main(d, d)
        schedule.every().day.at("02:00").do(job)
        print("📅 매일 02:00 자동 수집")
        while True:
            schedule.run_pending()
            time.sleep(60)
    elif len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        main()
