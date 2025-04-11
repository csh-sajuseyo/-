import pygetwindow as gw
import pyautogui
import pytesseract
from PIL import Image, ImageFilter, ImageOps
import time
import os
from ocr_similarity_corrector import correct_words_by_similarity

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def preprocess_image_optimized(pil_image):
    img = pil_image.convert("L")
    img = img.filter(ImageFilter.SHARPEN)
    img = ImageOps.autocontrast(img)
    width, height = img.size
    img = img.resize((width * 2, height * 2))
    return img

def ocr_whole_image(image, label):
    processed = preprocess_image_optimized(image)
    text = pytesseract.image_to_string(
        processed,
        lang="kor",
        config="--psm 6 --oem 3 -c preserve_interword_spaces=1"
    )
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    corrected_lines = [correct_words_by_similarity(line) for line in raw_lines]
    formatted = [f"[{label}] {line}" for line in corrected_lines]
    return formatted

def capture_and_extract_text():
    print("🔍 '에듀파인' 포함된 창 찾는 중...")

    windows = [w for w in gw.getAllWindows() if "에듀파인" in w.title]
    if not windows:
        print("❌ 에듀파인 창을 찾을 수 없습니다.")
        return False, "에듀파인 창을 찾을 수 없습니다."

    win = windows[0]
    win.activate()
    time.sleep(1)

    left, top, width, height = win.left, win.top, win.width, win.height
    crop_area = (left + 556, top + 375, 611, 319)

    print("📸 1차 캡처 진행 중...")
    img1 = pyautogui.screenshot(region=crop_area)
    os.makedirs("temp", exist_ok=True)
    img1_path = "temp/edu_ocr_capture_1.png"
    img1.save(img1_path)
    print("✅ 1차 캡처 저장 완료")

    pyautogui.moveTo(left + width // 2, top + height // 2)
    time.sleep(0.3)
    print("🔽 마우스 휠 스크롤 중 (1회)...")
    pyautogui.scroll(-500)
    time.sleep(0.3)

    print("📸 2차 캡처 진행 중...")
    img2 = pyautogui.screenshot(region=crop_area)
    img2_path = "temp/edu_ocr_capture_2.png"
    img2.save(img2_path)
    print("✅ 2차 캡처 저장 완료")

    print("🧪 OCR + 자동 보정 분석 중...")
    try:
        img1_loaded = Image.open(img1_path)
        img2_loaded = Image.open(img2_path)
        lines1 = ocr_whole_image(img1_loaded, "캡처1")
        lines2 = ocr_whole_image(img2_loaded, "캡처2")
    except Exception as e:
        return False, f"OCR 처리 중 오류 발생: {e}"

    final_text = "\n".join(lines1 + lines2)
    print("\n🔎 OCR 최종 추출 결과:")
    print("=" * 60)
    print(final_text)
    print("=" * 60)

    return True, final_text
