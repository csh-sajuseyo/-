
import sys
import os
import shutil
import atexit
from openpyxl import load_workbook
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QMessageBox, QFileDialog, QFrame
)
from PyQt5.QtGui import QFont, QCursor
from PyQt5.QtCore import Qt, QTimer
from sheet_api import get_today_pending_requests

class SajulgeyoApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("사줄게요 v1.0")
        self.setFixedSize(440, 570)
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

        # 상태 표시 라벨 초기화 (❔ 미확인 상태)
        self.contact_status = self.make_status_label()
        self.mall_status = self.make_status_label()
        self.sheet_status = self.make_status_label()
        self.edu_status = self.make_status_label()
        self.shot_status = self.make_status_label()

        # 버튼 + 상태 표시 구성 (숫자 정렬 포함)
        layout.addLayout(self.create_status_row("1.   📇 연락처 불러오기", self.load_contact, self.contact_status, "#e0f3ff", "#d0eaff"))
        layout.addLayout(self.create_status_row("2.   🔗 쇼핑몰 연결 시작하기", self.dummy_action, self.mall_status, "#f0e7ff", "#e2d6ff"))
        layout.addLayout(self.create_status_row("3.   📂 구매요청서 불러오기", self.load_sheet, self.sheet_status, "#fff9e0", "#fff1c2"))
        layout.addLayout(self.create_status_row("4.   📑 에듀파인 결재 확인 열기", self.dummy_action, self.edu_status, "#fff2e0", "#ffe5c2"))
        layout.addLayout(self.create_status_row("5.   🖼️ 구매 품목 스크린샷 확인", self.dummy_action, self.shot_status, "#e0fff4", "#c2ffe8"))

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #ccc; margin: 15px 0;")
        layout.addWidget(line)

        # 실행 버튼
        self.run_btn = QPushButton("🚀 물품 자동구매 시작하기")
        self.run_btn.setFont(QFont("맑은 고딕", 11, QFont.Bold))
        self.run_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.run_btn.setStyleSheet(
            "QPushButton {"
            " background-color: #28a745;"
            " color: white;"
            " padding: 14px;"
            " border-radius: 6px;"
            "}"
            "QPushButton:hover {"
            " background-color: #218838;"
            "}"
            "QPushButton:pressed {"
            " background-color: #1e7e34;"
            "}"
        )
        layout.addWidget(self.run_btn)

        self.setLayout(layout)
        QTimer.singleShot(0, self.center_on_screen)
        self.check_contact_file()

    def make_status_label(self):
        label = QLabel("❔")
        label.setFont(QFont("맑은 고딕", 14))
        label.setFixedWidth(30)
        label.setAlignment(Qt.AlignCenter)
        return label

    def create_status_row(self, text, func, status_label, base_color, hover_color):
        row = QHBoxLayout()
        btn = QPushButton(text)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn.setFont(QFont("맑은 고딕", 10))
        btn.setStyleSheet(
            f"QPushButton {{"
            f" padding: 8px;"
            f" background-color: {base_color};"
            f" border: 1px solid #aaa;"
            f" border-radius: 4px;"
            f" text-align: left;"
            f"}}"
            f"QPushButton:hover {{"
            f" background-color: {hover_color};"
            f"}}"
        )
        btn.clicked.connect(func)
        row.addWidget(btn)
        row.addWidget(status_label)
        return row

    def center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.center().x() - self.width() // 2,
            screen.center().y() - self.height() // 2
        )

    def dummy_action(self):
        QMessageBox.information(self, "준비 중", "해당 기능은 곧 지원됩니다!")

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
