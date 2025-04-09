import sys
import os
import shutil
import atexit
from openpyxl import load_workbook
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QMessageBox, QFileDialog
)
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt, QTimer
from sheet_api import get_today_pending_requests

class SajulgeyoApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("사줄게요 v1.0")
        self.setFixedSize(420, 500)
        self.setStyleSheet("background-color: #f7f9fb;")

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)

        self.title = QLabel("🛒 사줄게요 v1.0 - 자동구매 도우미")
        self.title.setFont(QFont("맑은 고딕", 14, QFont.Bold))
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)

        self.request_count_label = QLabel("오늘 요청 건수: ?")
        self.request_count_label.setFont(QFont("맑은 고딕", 12))
        self.request_count_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.request_count_label)

        # 연락처 불러오기
        self.contact_status = QLabel("❌")
        self.contact_status.setFont(QFont("맑은 고딕", 14))
        self.contact_status.setFixedWidth(30)
        self.contact_status.setAlignment(Qt.AlignCenter)

        contact_row = QHBoxLayout()
        self.contact_btn = QPushButton("📇 연락처 불러오기")
        self.contact_btn.setFont(QFont("맑은 고딕", 10))
        self.contact_btn.setStyleSheet("padding: 8px; background-color: white; border: 1px solid #aaa;")
        self.contact_btn.clicked.connect(self.load_contact)
        contact_row.addWidget(self.contact_btn)
        contact_row.addWidget(self.contact_status)
        layout.addLayout(contact_row)

        layout.addWidget(self.make_simple_button("🔗 쇼핑몰 연결 시작하기"))
        layout.addWidget(self.make_simple_button("📑 에듀파인 결재 확인 열기"))
        layout.addWidget(self.make_simple_button("🖼️ 스크린샷 확인"))

        # 구매요청서 불러오기 + 상태
        self.sheet_status = QLabel("❌")
        self.sheet_status.setFont(QFont("맑은 고딕", 14))
        self.sheet_status.setFixedWidth(30)
        self.sheet_status.setAlignment(Qt.AlignCenter)

        sheet_row = QHBoxLayout()
        self.sheet_btn = QPushButton("📂 구매요청서 불러오기")
        self.sheet_btn.setFont(QFont("맑은 고딕", 11))
        self.sheet_btn.setStyleSheet("background-color: #0078ff; color: white; padding: 8px;")
        self.sheet_btn.clicked.connect(self.load_sheet)
        sheet_row.addWidget(self.sheet_btn)
        sheet_row.addWidget(self.sheet_status)
        layout.addLayout(sheet_row)

        run_btn = QPushButton("🚀 물품 자동구매 시작하기")
        run_btn.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        run_btn.setStyleSheet("background-color: #28a745; color: white; padding: 12px;")
        layout.addWidget(run_btn)

        self.setLayout(layout)
        QTimer.singleShot(0, self.center_on_screen)

        self.check_contact_file()

    def make_simple_button(self, label):
        btn = QPushButton(label)
        btn.setFont(QFont("맑은 고딕", 10))
        btn.setStyleSheet("padding: 8px; background-color: white; border: 1px solid #aaa;")
        return btn

    def center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )

    def load_contact(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "연락처 엑셀파일 선택", "", "Excel Files (*.xlsx)")
        if file_path:
            try:
                dest_path = os.path.join("temp", "contacts.xlsx")
                shutil.copy(file_path, dest_path)
                self.check_contact_file()
            except Exception as e:
                QMessageBox.critical(self, "오류", f"연락처 저장 실패:\n{str(e)}")

    def check_contact_file(self):
        try:
            path = os.path.join("temp", "contacts.xlsx")
            if os.path.exists(path):
                wb = load_workbook(path)
                sheet = wb.active
                rows = list(sheet.iter_rows(min_row=1, max_row=sheet.max_row, values_only=True))
                if len(rows) > 0:
                    self.contact_status.setText("✅")
                    return
            self.contact_status.setText("❌")
        except:
            self.contact_status.setText("❌")

    def load_sheet(self):
        try:
            rows = get_today_pending_requests()
            count = len(rows)
            self.request_count_label.setText(f"오늘 요청 건수: {count}")
            self.sheet_status.setText("✅" if count > 0 else "❌")
            print("🔍 오늘 요청 목록:")
            for row in rows:
                print(row)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"구매요청서 불러오기 실패:\n{str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    CUSTOM_TEMP_DIR = os.path.join(BASE_DIR, "temp")
    os.makedirs(CUSTOM_TEMP_DIR, exist_ok=True)
    LOCK_FILE_PATH = os.path.join(CUSTOM_TEMP_DIR, "sajulgeyo.lock")

    if os.path.exists(LOCK_FILE_PATH):
        QMessageBox.critical(None, "중복 실행 차단", "❌ 사줄게요가 이미 실행 중입니다.")
        sys.exit(0)

    with open(LOCK_FILE_PATH, "w", encoding="utf-8") as f:
        f.write("🔐 락파일 생성됨")
    atexit.register(lambda: os.remove(LOCK_FILE_PATH) if os.path.exists(LOCK_FILE_PATH) else None)

    window = SajulgeyoApp()
    window.show()
    sys.exit(app.exec_())