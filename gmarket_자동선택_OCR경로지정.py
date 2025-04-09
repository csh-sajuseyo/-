
from selenium import webdriver
from selenium.webdriver.common.by import By
import pytesseract
from PIL import Image
import time
from difflib import SequenceMatcher
import traceback

# ✅ 디버깅 포트로 열린 크롬에 연결
options = webdriver.ChromeOptions()
options.debugger_address = "127.0.0.1:9223"
driver = webdriver.Chrome(options=options)

try:
    print("✅ 디버깅 크롬 연결 완료")
    driver.get("https://cart.gmarket.co.kr/")
    print("✅ 장바구니 페이지 로딩")
    time.sleep(4)

    # ✅ 전체선택 2회 클릭 → 체크 해제
    try:
        uncheck = driver.find_element(By.ID, "item_all_select")
        uncheck.click()
        time.sleep(1.2)
        uncheck.click()
        print("♻️ 전체선택 클릭 2회로 체크 초기화 완료")
        time.sleep(2.5)
    except Exception as e:
        print("⚠️ 전체선택 해제 실패 또는 요소 없음:", e)

    # ✅ 스크롤 끝까지 내리기
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    print("✅ 스크롤 완료 - 모든 품목 로딩 시도")

    # ✅ OCR로부터 키워드 추출
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    image_path = "지마켓.png"
    ocr_text = pytesseract.image_to_string(Image.open(image_path), lang="kor+eng")
    print("\n🔍 OCR 추출 키워드 (원본):\n", ocr_text)

    keywords = [line.strip() for line in ocr_text.split("\n") if len(line.strip()) >= 3]
    print("\n🔍 최종 키워드 목록:")
    for kw in keywords:
        print("-", kw)

    # ✅ 중복 방지를 위한 선택 title 집합
    selected_titles = set()

    # ✅ 품목 라벨 수집 및 키워드 기반 유사도 비교
    labels = driver.find_elements(By.CSS_SELECTOR, "label[for*=item_select]")
    print(f"\n📦 라벨 수집 완료: {len(labels)}개")

    for keyword in keywords:
        best_match = None
        best_score = 0

        for label in labels:
            title = label.text.strip()
            if not title or title in selected_titles:
                continue

            # 필수단어 필터
            if "청" in keyword and "청" not in title:
                continue
            if "0.38" in keyword and "0.38" not in title:
                continue

            score = SequenceMatcher(None, keyword, title).ratio()
            print(f"→ 키워드 '{keyword}' vs 품목 '{title}' → 유사도: {round(score, 3)}")

            if score > best_score:
                best_score = score
                best_match = label

        if best_match and best_score >= 0.3:
            driver.execute_script("arguments[0].scrollIntoView(true);", best_match)
            time.sleep(0.5)
            best_match.click()
            selected_titles.add(best_match.text.strip())
            print(f"✅ 선택됨: {best_match.text.strip()} (유사도: {round(best_score, 2)})")
        else:
            print(f"⚠️ '{keyword}'에 대해 선택된 후보 없음")

    if not selected_titles:
        print("\n⚠️ 아무 품목도 선택되지 않았습니다. OCR 키워드 또는 유사도 기준 확인 필요.")

    # ✅ 주문하기 버튼 클릭 시도
    try:
        order_btn = driver.find_element(By.CSS_SELECTOR, ".btn__item.btn__item--pay")
        order_btn.click()
        print("\n🧾 주문하기 버튼 클릭 완료")
    except Exception as e:
        print("\n⚠️ 주문하기 버튼 클릭 실패:", e)

except Exception as e:
    print("❌ 예외 발생:", e)
    traceback.print_exc()

input("⏸️ 종료 전 Enter를 누르세요")
