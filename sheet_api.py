from __future__ import print_function
import datetime
import os.path
import pytz

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SPREADSHEET_ID = '1KbY0lHEGGrkVNWER5iJsGM4dIYp69r7cJbzkPOdr3xk'
RANGE_NAME = '시트1!A2:H'  # 수정된 탭 이름 반영

def get_today_request_count():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    seoul_tz = pytz.timezone('Asia/Seoul')
    today = datetime.datetime.now(seoul_tz).date()

    result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
    values = result.get('values', [])

    count = 0
    for row in values:
        if len(row) >= 5:
            try:
                request_date = datetime.datetime.fromisoformat(row[4]).date()
                if request_date == today:
                    count += 1
            except:
                continue
    return count



def get_today_pending_requests():
    """
    오늘 날짜(G열) + '구매완료'가 아닌 요청만 반환
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    seoul_tz = pytz.timezone('Asia/Seoul')
    today = datetime.datetime.now(seoul_tz).date()

    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE_NAME
    ).execute()
    values = result.get('values', [])

    filtered = []
    for row in values:
        if len(row) >= 7:
            try:
                request_date = datetime.datetime.fromisoformat(row[6].strip()).date()
                if request_date == today and (len(row) < 8 or row[7].strip() != "구매완료"):
                    filtered.append(row)
            except Exception:
                continue
    return filtered