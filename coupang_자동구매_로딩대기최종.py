
print("✅ 자동구매 시작 - 로딩 완료까지 대기 포함한 최종 버전")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from difflib import SequenceMatcher
import time

ocr_keywords = {
    "파티공구 만들기 가면": [],
    "여왕반가면": [],
    "모닝글로리 프로마하펜 청": ["0.38"]
}

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

options = Options()
options.debugger_address = "127.0.0.1:9222"

try:
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    print("✅ ChromeDriver 연결 성공")
except Exception as e:
    print("❌ Chrome 연결 실패:", e)
    exit()

try:
    driver.get("https://cart.coupang.com/cartView.pang")
    print("✅ 장바구니 페이지 로딩 성공")
    time.sleep(3)
except Exception as e:
    print("❌ 장바구니 이동 실패:", e)
    exit()

try:
    checkboxes = driver.find_elements(By.CSS_SELECTOR, "input.dealSelectChk[type='checkbox']")
    print(f"📦 체크박스 수: {len(checkboxes)}")

    for checkbox in checkboxes:
        try:
            if checkbox.is_selected():
                driver.execute_script("arguments[0].click();", checkbox)
                time.sleep(0.1)
        except Exception as e:
            print(f"⚠️ 체크 해제 실패 항목 있음: {e}")

    # ✅ 해제 완료될 때까지 기다림
    print("⏳ 해제 반영 대기 중...")
    timeout = 15
    start = time.time()
    while True:
        remaining = [cb for cb in checkboxes if cb.is_selected()]
        if not remaining:
            break
        if time.time() - start > timeout:
            print("⚠️ 일부 품목이 시간 내에 해제되지 않음")
            break
        time.sleep(0.5)
    print("♻️ 기존 체크 해제 완료")

    # ✅ 요소 재수집
    checkboxes = driver.find_elements(By.CSS_SELECTOR, "input.dealSelectChk[type='checkbox']")

    selected_count = 0
    clicked_titles = set()

    for keyword, required_terms in ocr_keywords.items():
        candidates = []

        for checkbox in checkboxes:
            title = checkbox.get_attribute("title").strip()
            if all(term in title for term in required_terms):
                candidates.append((checkbox, title))

        print(f"🔎 '{keyword}' 관련 후보군 {len(candidates)}개")

        best_score = 0
        best_match = None
        best_title = ""

        for checkbox, title in candidates:
            if title in clicked_titles:
                continue
            score = similar(title, keyword)
            if score > best_score:
                best_score = score
                best_match = checkbox
                best_title = title

        if best_match and best_score > 0.3:
            try:
                driver.execute_script("arguments[0].click();", best_match)
                selected_count += 1
                clicked_titles.add(best_title)
                print(f"✅ 선택됨: {best_title} (유사도: {best_score:.2f})")
            except Exception as e:
                print(f"❌ 클릭 실패: {best_title} → {e}")
        else:
            print(f"⚠️ '{keyword}'에 대해 선택된 후보 없음")

    print(f"\n✅ 총 {selected_count}개 품목 선택 완료")

except Exception as e:
    print(f"❌ 체크박스 처리 중 오류: {e}")

print("🧾 주문하기 버튼 클릭 중...")
try:
    order_btn = driver.find_element(By.ID, "btnPay")
    driver.execute_script("arguments[0].click();", order_btn)
    print("✅ 주문하기 버튼 클릭 완료")
except Exception as e:
    print(f"❌ 주문하기 실패: {e}")

input("🔚 Enter 키를 누르면 종료됩니다.")
