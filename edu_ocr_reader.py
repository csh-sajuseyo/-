
import pygetwindow as gw
import pyautogui
import pytesseract
from PIL import Image
import time
import os

def capture_and_extract_text():
    print("🔍 '에듀파인' 포함된 창 찾는 중...")

    # 1. 에듀파인 창 검색
    windows = [w for w in gw.getAllWindows() if "에듀파인" in w.title]
    if not windows:
        print("❌ 에듀파인 창이 열려 있지 않거나 찾을 수 없습니다.")
        return False, "에듀파인 창을 찾을 수 없습니다."

    win = windows[0]
    win.activate()
    time.sleep(1)

    # 2. 사각형 9시 방향 이동 + 3시 방향 크기 줄임
    left, top, width, height = win.left, win.top, win.width, win.height
    crop_area = (left + 556, top + 375, 611, 319)

    # 3. 1차 캡처
    print("📸 1차 캡처 진행 중...")
    img1 = pyautogui.screenshot(region=crop_area)
    os.makedirs("temp", exist_ok=True)
    img1.save("temp/edu_ocr_capture_1.png")
    print("✅ 1차 캡처 저장 완료")

    # 4. 마우스 커서 중앙 이동
    center_x = left + width // 2
    center_y = top + height // 2
    pyautogui.moveTo(center_x, center_y)
    time.sleep(0.3)

    # 5. 마우스 휠 스크롤 1회
    print("🔽 마우스 휠 스크롤 중 (1회)...")
    pyautogui.scroll(-500)
    time.sleep(0.3)

    # 6. 2차 캡처
    print("📸 2차 캡처 진행 중...")
    img2 = pyautogui.screenshot(region=crop_area)
    img2.save("temp/edu_ocr_capture_2.png")
    print("✅ 2차 캡처 저장 완료")

    # 7. OCR 추출
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    text1 = pytesseract.image_to_string(img1, lang='kor+eng')
    text2 = pytesseract.image_to_string(img2, lang='kor+eng')

    # 8. 결과 병합 출력
    print("\n🔎 OCR 추출 결과 (1차 + 2차):")
    print("=" * 60)
    print(text1.strip())
    print("-" * 60)
    print(text2.strip())
    print("=" * 60)

    return True, text1 + "\n" + text2
