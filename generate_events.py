#!/usr/bin/env python3
"""
メイカーイベント静的サイト生成スクリプト
Maker Event Static Site Generator
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin
import io
import zipfile

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser
from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

# サイト情報（機械可読出力で使用）
SITE_URL = "https://shinichi-ohki.github.io/maker_event/"
ICS_UID_DOMAIN = "shinichi-ohki.github.io"

# 「(予定)」「(未定)」などの未確定表記の検出パターン
TENTATIVE_PATTERN = re.compile(r'[（(]\s*(?:予定|未定)\s*[)）]')


class Event(BaseModel):
    """イベントデータモデル"""
    name: str
    date: Optional[str] = None
    location: str
    country: str
    description: str = ""
    url: str = ""
    image_url: str = ""
    is_japan: bool = False
    parsed_date: Optional[datetime] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    parsed_date_from: Optional[datetime] = None
    parsed_date_to: Optional[datetime] = None
    # 機械可読出力 (events.json / events.ics) 用の生データ
    venue: str = ""
    region: str = ""
    x_url: str = ""
    date_text: str = ""
    is_tentative: bool = False
    
    def model_post_init(self, __context):
        """初期化後処理"""
        if self.date:
            try:
                self.parsed_date = parser.parse(self.date)
            except:
                self.parsed_date = None
        
        # 開始日と終了日をパース
        if self.date_from:
            try:
                self.parsed_date_from = parser.parse(self.date_from)
                # parsed_dateを開始日に設定（ソート用）
                if not self.parsed_date:
                    self.parsed_date = self.parsed_date_from
            except:
                self.parsed_date_from = None
        
        if self.date_to:
            try:
                self.parsed_date_to = parser.parse(self.date_to)
            except:
                self.parsed_date_to = None
        
        self.is_japan = self.country.lower() in ['japan', '日本', 'jp']


def load_country_mapping() -> Dict[str, str]:
    """国名マッピングファイルを読み込み"""
    mapping_file = Path("country_mapping.json")
    if mapping_file.exists():
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  国名マッピングファイル読み込みエラー: {e}")
    return {}


def extract_country_from_region(region: str, country_mapping: Dict[str, str]) -> str:
    """地域列から国名を抽出

    対応パターン:
    - "パリ(フランス)"          → 括弧内が国名 → "France"
    - "武蔵小杉(神奈川)"        → 括弧内が都道府県 → "Japan"
    - "バンコク(タイ)"          → 括弧内が国名 → "Thailand"
    - "ウランバートル(モンゴル)" → 括弧内が国名（要 country_mapping.json 登録）→ "Mongolia"
    - "タイ(バンコク)"          → 括弧の外が国名の場合は外側も照会 → "Thailand"
    - 括弧なし                  → 地域全体が国名ならそれを使用、それ以外は "Japan"（デフォルト）
    """
    # 括弧内の国名・都道府県名を抽出（半角・全角括弧に対応）
    match = re.search(r'[（(]([^)）]+)[)）]', region)
    if match:
        inner = match.group(1).strip()
        if inner in country_mapping:
            return country_mapping[inner]
        # 括弧内が国名でない場合（都市名など）、括弧の外側を国名として照会
        outer = re.sub(r'[（(][^)）]*[)）]', '', region).strip()
        if outer in country_mapping:
            return country_mapping[outer]
        # どちらにも無い場合は括弧内の表記をそのまま返す（判定不能を隠さない）
        return inner

    # 括弧なし: 地域全体が国名に一致すればそれを使う（例: "東京" → Japan, "タイ" → Thailand）
    if region in country_mapping:
        return country_mapping[region]
    return "Japan"


def get_spreadsheet_csv_url(sheet_url: str) -> str:
    """Google SheetsのURLをCSVエクスポート用URLに変換"""
    if 'docs.google.com/spreadsheets' in sheet_url:
        sheet_id = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', sheet_url)
        if sheet_id:
            return f"https://docs.google.com/spreadsheets/d/{sheet_id.group(1)}/export?format=csv"
    return sheet_url


def load_last_state() -> Dict:
    """前回の状態をファイルから読み込み"""
    state_file = Path(".last_state.json")
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️  前回の状態ファイル読み込みエラー: {e}")
    return {}


def save_last_state(state: Dict) -> None:
    """現在の状態をファイルに保存"""
    state_file = Path(".last_state.json")
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  状態ファイル保存エラー: {e}")


def get_content_hash(content: str) -> str:
    """コンテンツのハッシュ値を計算"""
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def has_spreadsheet_changed(sheet_url: str) -> tuple[bool, str]:
    """スプレッドシートの変更をチェック
    
    Returns:
        tuple[bool, str]: (変更があったか, 現在のハッシュ値)
    """
    csv_url = get_spreadsheet_csv_url(sheet_url)
    
    try:
        # 現在のスプレッドシート内容を取得
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'
        current_content = response.text
        current_hash = get_content_hash(current_content)
        
        # 前回の状態を読み込み
        last_state = load_last_state()
        last_hash = last_state.get('content_hash', '')
        
        # 変更をチェック
        has_changed = current_hash != last_hash
        
        if has_changed:
            print(f"📝 スプレッドシートの変更を検出: {last_hash[:8]} → {current_hash[:8]}")
        else:
            print(f"✅ スプレッドシートに変更なし: {current_hash[:8]}")
        
        return has_changed, current_hash
        
    except Exception as e:
        print(f"❌ スプレッドシート変更チェックエラー: {e}")
        return True, ""  # エラー時は更新を実行


def should_update_page(sheet_url: str) -> tuple[bool, str]:
    """ページ更新が必要かチェック（スプレッドシート変更 + 時間経過）
    
    Returns:
        tuple[bool, str]: (更新が必要か, 現在のハッシュ値)
    """
    # スプレッドシートの変更をチェック
    spreadsheet_changed, current_hash = has_spreadsheet_changed(sheet_url)
    
    if spreadsheet_changed:
        return True, current_hash
    
    # スプレッドシートに変更がない場合、時間経過による更新が必要かチェック
    last_state = load_last_state()
    last_updated_str = last_state.get('last_updated', '')
    
    if not last_updated_str:
        print("🕒 前回更新時刻が不明のため、更新を実行")
        return True, current_hash
    
    try:
        last_updated = datetime.fromisoformat(last_updated_str)
        hours_since_update = (datetime.now() - last_updated).total_seconds() / 3600
        
        # 12時間以上経過している場合は時間経過による更新を実行
        if hours_since_update >= 12:
            print(f"🕒 前回更新から{hours_since_update:.1f}時間経過: 時間経過による更新を実行")
            return True, current_hash
        else:
            print(f"⏰ 前回更新から{hours_since_update:.1f}時間: 更新不要")
            return False, current_hash
            
    except Exception as e:
        print(f"⚠️  時間チェックエラー: {e}, 安全のため更新を実行")
        return True, current_hash


def auto_commit_and_push() -> bool:
    """変更をGitリポジトリにコミット・プッシュ"""
    try:
        # 変更があるかチェック
        result = subprocess.run(['git', 'diff', '--quiet'], capture_output=True)
        staged_result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True)
        
        if result.returncode == 0 and staged_result.returncode == 0:
            print("📝 Gitリポジトリに変更がありません")
            return False
        
        # ファイルをステージング
        files_to_add = ['index.html', 'ogp_image.png', '.last_state.json',
                        'events.json', 'events.ics', 'llms.txt']
        for file in files_to_add:
            if Path(file).exists():
                subprocess.run(['git', 'add', file], check=True)
        
        # コミットメッセージを生成
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S JST')
        commit_message = f"""サイト更新

🤖 自動更新 - {timestamp}

Generated with [Claude Code](https://claude.ai/code)"""
        
        # コミット
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        print(f"✅ 変更をコミットしました")
        
        # プッシュ
        subprocess.run(['git', 'push'], check=True)
        print(f"🚀 リポジトリにプッシュしました")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Git操作エラー: {e}")
        return False
    except Exception as e:
        print(f"❌ 予期しないエラー: {e}")
        return False


def fetch_events_from_sheet(sheet_url: str) -> List[Dict]:
    """Google Sheetsからイベントデータを取得"""
    csv_url = get_spreadsheet_csv_url(sheet_url)
    
    try:
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()
        
        # UTF-8エンコーディングを明示的に指定
        response.encoding = 'utf-8'
        csv_content = response.text
        reader = csv.DictReader(csv_content.splitlines())
        
        events = []
        for row in reader:
            if row and any(row.values()):
                events.append(dict(row))
        
        return events
    except Exception as e:
        print(f"Error fetching spreadsheet data: {e}")
        return []


def extract_image_from_url(url: str) -> str:
    """URLからOGP画像やファビコンを取得"""
    if not url or not url.startswith('http'):
        return ""
    
    try:
        # User-Agentを設定してリクエスト
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # OGP画像を優先的に取得
        og_image = soup.find('meta', property='og:image')
        if og_image and isinstance(og_image, Tag):
            content = og_image.get('content')
            if content and isinstance(content, str):
                image_url = content
                # 相対URLの場合は絶対URLに変換
                if image_url.startswith('/'):
                    image_url = urljoin(url, image_url)
                return image_url
        
        # Twitter Card画像を試す
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and isinstance(twitter_image, Tag):
            content = twitter_image.get('content')
            if content and isinstance(content, str):
                image_url = content
                if image_url.startswith('/'):
                    image_url = urljoin(url, image_url)
                return image_url
        
        # ファビコンを最後の手段として取得
        favicon = soup.find('link', rel='icon') or soup.find('link', rel='shortcut icon')
        if favicon and isinstance(favicon, Tag):
            href = favicon.get('href')
            if href and isinstance(href, str):
                favicon_url = href
                if favicon_url.startswith('/'):
                    favicon_url = urljoin(url, favicon_url)
                return favicon_url
        
        return ""
        
    except Exception as e:
        print(f"画像取得エラー ({url}): {e}")
        return ""


def download_noto_font() -> Optional[str]:
    """Noto Sans JP フォントをダウンロード"""
    font_path = "NotoSansJP-Regular.ttf"
    
    # フォントファイルが既に存在する場合はそれを使用
    if Path(font_path).exists():
        return font_path
    
    # Google Fonts APIから最新のフォントURLを取得
    try:
        print("📡 Google Fonts APIからフォントURL取得中...")
        css_response = requests.get(
            "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400&display=swap",
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            timeout=10
        )
        css_response.raise_for_status()
        
        # CSSからフォントURLを抽出
        import re
        font_urls = re.findall(r'https://fonts\.gstatic\.com[^)]+\.ttf', css_response.text)
        if font_urls:
            print(f"✅ {len(font_urls)}個のフォントURLを発見")
        else:
            # フォールバック用の固定URL
            font_urls = [
                "https://fonts.gstatic.com/s/notosansjp/v54/-F6jfjtqLzI2JPCgQBnw7HFyzSD-AsregP8VFBEj75s.ttf",
                "https://fonts.gstatic.com/s/notosansjp/v54/-F6jfjtqLzI2JPCgQBnw7HFyzSD-AsregP8VFPYk75s.ttf"
            ]
            print("⚠️ フォールバックURLを使用")
            
    except Exception as e:
        print(f"❌ Google Fonts API取得エラー: {e}")
        # 最終フォールバック
        font_urls = [
            "https://fonts.gstatic.com/s/notosansjp/v54/-F6jfjtqLzI2JPCgQBnw7HFyzSD-AsregP8VFBEj75s.ttf"
        ]
    
    for i, font_url in enumerate(font_urls, 1):
        try:
            print(f"📥 Noto Sans JP フォントをダウンロード中... ({i}/{len(font_urls)})")
            print(f"   URL: {font_url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(font_url, headers=headers, timeout=30, stream=True)
            response.raise_for_status()
            
            # コンテンツタイプを確認
            content_type = response.headers.get('content-type', '')
            if 'font' not in content_type and 'octet-stream' not in content_type:
                print(f"⚠️  予期しないコンテンツタイプ: {content_type}")
                continue
            
            with open(font_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # ファイルサイズを確認
            file_size = Path(font_path).stat().st_size
            if file_size < 100000:  # 100KB未満の場合は無効とみなす
                print(f"⚠️  ダウンロードしたファイルサイズが小さすぎます: {file_size} bytes")
                Path(font_path).unlink(missing_ok=True)
                continue
            
            print(f"✅ フォントを保存: {font_path} ({file_size:,} bytes)")
            return font_path
            
        except Exception as e:
            print(f"❌ URL {i} でエラー: {e}")
            continue
    
    # 全てのURLが失敗した場合
    print("⚠️  すべてのフォントダウンロードが失敗しました。システムフォントを使用します")
    return None


def create_ogp_image(events: List[Event]) -> str:
    """OGP用のガントチャート風画像を生成"""
    try:
        # 画像サイズ (1200x630 - OGP推奨サイズ)
        width, height = 1200, 630
        
        # 背景色（深めの色で見やすく）
        img = Image.new('RGB', (width, height), color='#1a1a2e')
        draw = ImageDraw.Draw(img)
        
        # フォントの設定
        font_path = download_noto_font()
        
        try:
            if font_path and Path(font_path).exists():
                # Noto Sans JP Boldフォントを使用（サイズをさらに大きく、太く）
                title_font = ImageFont.truetype(font_path, 36)
                event_font = ImageFont.truetype(font_path, 20)
                date_font = ImageFont.truetype(font_path, 16)
                stats_font = ImageFont.truetype(font_path, 18)
            else:
                # フォールバック（デフォルトフォント）
                title_font = ImageFont.load_default()
                event_font = ImageFont.load_default()
                date_font = ImageFont.load_default()
                stats_font = ImageFont.load_default()
        except Exception as e:
            print(f"フォント読み込みエラー: {e}")
            title_font = event_font = date_font = stats_font = ImageFont.load_default()
        
        # ヘッダー部分
        header_height = 80
        draw.rectangle([0, 0, width, header_height], fill='#16213e')
        
        # タイトル - 太字効果
        title = "Upcoming Maker Events Timeline"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (width - title_width) // 2
        
        # 太字効果のための重複描画
        for dx in range(3):
            for dy in range(3):
                if dx == 1 and dy == 1:
                    continue
                draw.text((title_x + dx - 1, 25 + dy - 1), title, fill='white', font=title_font)
        draw.text((title_x, 25), title, fill='white', font=title_font)
        
        # 統計情報行を削除（コメントアウト）
        
        # ガントチャート部分の設定
        chart_start_y = header_height + 20
        chart_height = height - chart_start_y - 40
        row_height = 40
        max_rows = chart_height // row_height
        
        # 表示するイベントを選択（最大12個程度）
        display_events = events[:min(12, len(events))]
        
        if not display_events:
            # イベントがない場合のメッセージ
            no_events_text = "No upcoming events scheduled"
            no_events_bbox = draw.textbbox((0, 0), no_events_text, font=event_font)
            no_events_width = no_events_bbox[2] - no_events_bbox[0]
            no_events_x = (width - no_events_width) // 2
            draw.text((no_events_x, height // 2), no_events_text, fill='#8892b0', font=event_font)
        else:
            # 日付範囲を計算
            earliest_date = min(e.parsed_date for e in display_events if e.parsed_date)
            latest_date = max(e.parsed_date for e in display_events if e.parsed_date)
            
            if earliest_date and latest_date:
                date_range = (latest_date - earliest_date).days
                if date_range == 0:
                    date_range = 1
                
                # タイムライン軸の設定
                timeline_start_x = 200
                timeline_width = width - timeline_start_x - 50
                
                # 月のヘッダーを描画
                current_month = None
                month_positions = []
                
                for i, event in enumerate(display_events):
                    if not event.parsed_date:
                        continue
                        
                    # イベントの位置を計算
                    days_from_start = (event.parsed_date - earliest_date).days
                    x_pos = timeline_start_x + (days_from_start / date_range) * timeline_width
                    y_pos = chart_start_y + (i % max_rows) * row_height
                    
                    # 月が変わった場合の区切り線
                    event_month = event.parsed_date.strftime('%Y-%m')
                    if event_month != current_month:
                        month_positions.append((x_pos, event_month))
                        current_month = event_month
                
                # 月の区切り線を描画
                for pos, month in month_positions:
                    draw.line([pos, chart_start_y, pos, height - 40], fill='#16213e', width=2)
                    month_text = datetime.strptime(month, '%Y-%m').strftime('%m月')
                    
                    # 月の文字も太字効果
                    for dx in range(2):
                        for dy in range(2):
                            if dx == 0 and dy == 0:
                                continue
                            draw.text((pos + 5 + dx, chart_start_y - 15 + dy), month_text, fill='#8892b0', font=date_font)
                    draw.text((pos + 5, chart_start_y - 15), month_text, fill='#8892b0', font=date_font)
                
                # イベントバーを描画
                for i, event in enumerate(display_events):
                    if not event.parsed_date:
                        continue
                        
                    y_pos = chart_start_y + (i % max_rows) * row_height
                    
                    # イベントの位置を計算
                    days_from_start = (event.parsed_date - earliest_date).days
                    x_pos = timeline_start_x + (days_from_start / date_range) * timeline_width
                    
                    # バーの色（日本か海外かで色分け）
                    bar_color = '#667eea' if event.is_japan else '#f093fb'
                    
                    # イベントバーを描画（複数日程は丸角長方形、単一日は正円）
                    dot_size = 8
                    if event.parsed_date_to and event.parsed_date_from != event.parsed_date_to:
                        # 複数日程の場合は丸角長方形（角丸長方形）
                        width_extend = 8
                        left = x_pos - dot_size - width_extend
                        right = x_pos + dot_size + width_extend
                        top = y_pos + 10
                        bottom = y_pos + 26
                        
                        # 中央の長方形
                        draw.rectangle([left + dot_size, top, right - dot_size, bottom - 1], 
                                     fill=bar_color, outline=None)
                        # 左の半円
                        draw.ellipse([left, top, left + dot_size * 2, bottom], 
                                   fill=bar_color, outline=None)
                        # 右の半円
                        draw.ellipse([right - dot_size * 2, top, right, bottom], 
                                   fill=bar_color, outline=None)
                        
                        # 輪郭線
                        # 中央の長方形の上下線
                        draw.line([left + dot_size, top, right - dot_size, top], fill='white', width=2)
                        draw.line([left + dot_size, bottom - 1, right - dot_size, bottom - 1], fill='white', width=2)
                        # 左の半円の輪郭
                        draw.arc([left, top, left + dot_size * 2, bottom], start=90, end=270, fill='white', width=2)
                        # 右の半円の輪郭
                        draw.arc([right - dot_size * 2, top, right, bottom], start=270, end=90, fill='white', width=2)
                    else:
                        # 単一日の場合は正円
                        draw.ellipse([x_pos - dot_size, y_pos + 10, x_pos + dot_size, y_pos + 26], 
                                   fill=bar_color, outline='white', width=2)
                    
                    # イベント名を描画（左側）- 太字効果のため少しずらして重複描画
                    event_name = event.name
                    if len(event_name) > 25:
                        event_name = event_name[:22] + "..."
                    
                    # 太字効果のための重複描画
                    for dx in range(2):
                        for dy in range(2):
                            if dx == 0 and dy == 0:
                                continue
                            draw.text((20 + dx, y_pos + 12 + dy), event_name, fill='white', font=event_font)
                    draw.text((20, y_pos + 12), event_name, fill='white', font=event_font)
                    
                    # 日付を描画（ドットの右側）- 太字効果
                    # 複数日程対応
                    if event.parsed_date_to and event.parsed_date_from and event.parsed_date_from != event.parsed_date_to:
                        if event.parsed_date_from.month == event.parsed_date_to.month:
                            # 同月の場合: 08/02-03
                            date_text = f"{event.parsed_date_from.strftime('%m/%d')}-{event.parsed_date_to.strftime('%d')}"
                        else:
                            # 月またぎの場合: 08/31-09/01
                            date_text = f"{event.parsed_date_from.strftime('%m/%d')}-{event.parsed_date_to.strftime('%m/%d')}"
                    else:
                        # 単一日の場合
                        if event.parsed_date_from:
                            date_text = event.parsed_date_from.strftime('%m/%d')
                        elif event.parsed_date:
                            date_text = event.parsed_date.strftime('%m/%d')
                        else:
                            date_text = "TBD"
                    
                    for dx in range(2):
                        for dy in range(2):
                            if dx == 0 and dy == 0:
                                continue
                            draw.text((x_pos + 15 + dx, y_pos + 12 + dy), date_text, fill='#8892b0', font=date_font)
                    draw.text((x_pos + 15, y_pos + 12), date_text, fill='#8892b0', font=date_font)
        
        # フッター
        footer_text = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        draw.text((20, height - 30), footer_text, fill='#8892b0', font=stats_font)
        
        # 画像を保存
        output_path = "ogp_image.png"
        img.save(output_path, quality=95)
        return output_path
        
    except Exception as e:
        print(f"OGP画像生成エラー: {e}")
        return ""


def parse_events(raw_events: List[Dict]) -> List[Event]:
    """生データをEventオブジェクトに変換"""
    events = []
    current_year = None

    # 国名マッピングを読み込み
    country_mapping = load_country_mapping()

    for raw in raw_events:
        try:
            # 実際のスプレッドシート列名に基づくマッピング
            name = raw.get('名称', '').strip()
            location = raw.get('場所', '').strip()
            region = raw.get('地域', '').strip()
            date_from = raw.get('から', '').strip()
            date_to = raw.get('まで', '').strip()
            url = raw.get('URL', '').strip()
            x_url = raw.get('X', '').strip()
            description = raw.get('備考', '').strip()
            
            # 年のヘッダー行を検出
            if name.endswith('年') and not location and not date_from:
                # 年を抽出（例：「2025年」→「2025」）
                try:
                    current_year = int(name.replace('年', ''))
                    print(f"📅 年ヘッダーを検出: {current_year}年")
                    continue
                except:
                    continue
            
            # 空のデータをスキップ
            if not name or not location:
                continue
            
            # 年が設定されていない場合はデフォルト年を使用
            if current_year is None:
                current_year = datetime.now().year
                print(f"⚠️  年ヘッダーが見つからないため、現在年を使用: {current_year}")
            
            # 日付の組み立て
            date_str = ""
            date_from_full = ""
            date_to_full = ""
            
            if date_from:
                # 既に年が含まれているかチェック
                if '/' in date_from and len(date_from.split('/')) >= 3:
                    # 既に年月日形式の場合はそのまま使用
                    date_from_full = date_from
                else:
                    # 月日のみの場合は年を追加
                    date_from_full = f"{current_year}/{date_from}"
                date_str = date_from_full
                
            if date_to:
                # 既に年が含まれているかチェック
                if '/' in date_to and len(date_to.split('/')) >= 3:
                    # 既に年月日形式の場合はそのまま使用
                    date_to_full = date_to
                else:
                    # 月日のみの場合は年を追加
                    date_to_full = f"{current_year}/{date_to}"
            
            # locationとregionを組み合わせ
            full_location = f"{location}, {region}" if region else location

            # 地域列から国名を抽出（括弧内の国名を使用）
            country = extract_country_from_region(region, country_mapping) if region else "Japan"
            
            # 画像URLは後で今後のイベントのみに対して取得する
            image_url = ""
            
            # 機械可読出力用の生データ（日付はシートの元表記を保持）
            venue = location
            if date_from and date_to and date_from != date_to:
                date_text = f"{date_from}-{date_to}"
            elif date_from:
                date_text = date_from
            else:
                date_text = date_to
            is_tentative = bool(
                TENTATIVE_PATTERN.search(name)
                or TENTATIVE_PATTERN.search(date_from)
                or TENTATIVE_PATTERN.search(date_to)
            )

            event_data = {
                'name': name,
                'date': date_str,
                'location': full_location,
                'country': country,
                'description': description,
                'url': url,
                'image_url': image_url,
                'date_from': date_from_full,
                'date_to': date_to_full,
                'venue': venue,
                'region': region,
                'x_url': x_url,
                'date_text': date_text,
                'is_tentative': is_tentative,
            }
            
            if event_data['name'] and event_data['location']:
                event = Event(**event_data)
                events.append(event)
                
        except Exception as e:
            print(f"Error parsing event: {e}")
            continue
    
    return events


def fetch_event_image(event: Event) -> Event:
    """単一イベントの画像を取得"""
    if event.url and not event.image_url:
        print(f"🖼️  画像取得中: {event.name}")
        event.image_url = extract_image_from_url(event.url)
    return event


def filter_upcoming_events(events: List[Event], days_ahead: int = 730) -> List[Event]:
    """今後開催予定のイベントをフィルタリング"""
    now = datetime.now()
    cutoff_date = now + timedelta(days=days_ahead)
    # 今日の開始時刻（午前0時）を基準にする
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    upcoming = []
    for event in events:
        # 複数日開催の場合は終了日も考慮
        event_end_date = event.parsed_date_to if event.parsed_date_to else event.parsed_date
        event_start_date = event.parsed_date_from if event.parsed_date_from else event.parsed_date
        
        # イベントが今日以降に終了する、または今後開始するイベントを含める
        if event_end_date and event_end_date >= today_start and event_start_date and event_start_date <= cutoff_date:
            upcoming.append(event)
    
    # 今後のイベントのみサムネイルを並行取得
    events_needing_images = [event for event in upcoming if event.url and not event.image_url]
    
    if events_needing_images:
        print(f"🖼️  {len(events_needing_images)}件の画像を並行取得中...")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for i, event in enumerate(events_needing_images):
                if i > 0:
                    time.sleep(0.1)  # 短い間隔でタスクを開始
                future = executor.submit(fetch_event_image, event)
                futures.append(future)
            
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"画像取得エラー: {e}")
    
    return sorted(upcoming, key=lambda x: x.parsed_date or datetime.max)


def select_undated_events(events: List[Event]) -> List[Event]:
    """日付が確定していないイベント（「2027年春頃」等でパース失敗）を抽出

    これらは events.json にのみ含め（start_date/end_date = null、date_text に元表記）、
    HTML と events.ics には含めない。
    """
    return [e for e in events if e.parsed_date is None]


def format_event_date(event: Event) -> str:
    """イベント日付を適切にフォーマット"""
    if not event.parsed_date_from:
        return ""
    
    # 開始日のフォーマット
    start_date = event.parsed_date_from
    
    # 終了日がない、または開始日と同じ場合は単一日
    if not event.parsed_date_to or event.parsed_date_from.date() == event.parsed_date_to.date():
        if event.is_japan:
            return start_date.strftime('%Y年%m月%d日')
        else:
            return start_date.strftime('%B %d, %Y')
    
    # 複数日開催の場合
    end_date = event.parsed_date_to
    
    if event.is_japan:
        # 同じ月の場合
        if start_date.month == end_date.month:
            return f"{start_date.strftime('%Y年%m月%d日')}〜{end_date.strftime('%d日')}"
        else:
            # 月をまたぐ場合
            return f"{start_date.strftime('%Y年%m月%d日')}〜{end_date.strftime('%m月%d日')}"
    else:
        # 英語表記
        if start_date.month == end_date.month:
            return f"{start_date.strftime('%B %d')}-{end_date.strftime('%d, %Y')}"
        else:
            return f"{start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')}"


def event_public_id(name: str) -> str:
    """イベント名から安定した公開IDを生成（名前ベースなので日付変更でも不変）"""
    normalized = unicodedata.normalize('NFC', name.strip())
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]


def event_to_dict(event: Event) -> Dict:
    """events.json 用のイベント dict を生成"""
    start_date = event.parsed_date_from.strftime('%Y-%m-%d') if event.parsed_date_from else None
    end_dt = event.parsed_date_to or event.parsed_date_from
    end_date = end_dt.strftime('%Y-%m-%d') if end_dt else None
    return {
        'id': event_public_id(event.name),
        'name': event.name,
        'start_date': start_date,
        'end_date': end_date,
        'date_text': event.date_text,
        'is_tentative': event.is_tentative,
        'country': event.country,
        'region': event.region,
        'venue': event.venue,
        'url': event.url or None,
        'x_url': event.x_url or None,
    }


def generate_events_json_text(events: List[Event], last_changed_at: datetime) -> str:
    """events.json の内容を生成（同じデータなら常に同じバイト列になる決定論的出力）"""
    data = {
        'schema_version': 1,
        'site': SITE_URL,
        'updated_at': last_changed_at.isoformat(),
        'events': [event_to_dict(e) for e in events],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def _escape_ical_text(value: str) -> str:
    """RFC 5545 3.3.11 の TEXT エスケープ"""
    value = value.replace('\\', '\\\\')
    value = value.replace(';', '\\;')
    value = value.replace(',', '\\,')
    value = value.replace('\r\n', '\\n')
    value = value.replace('\r', '\\n')
    value = value.replace('\n', '\\n')
    return value


def _fold_ical_line(line: str, first_limit: int = 75, cont_limit: int = 74) -> str:
    """RFC 5545 3.1 の行折り返し（75オクテット、UTF-8 の文字境界で分割）"""
    octets = line.encode('utf-8')
    if len(octets) <= first_limit:
        return line
    parts = []
    limit = first_limit
    while octets:
        chunk = octets[:limit]
        # マルチバイト文字の途中で切れないよう、デコード可能な最長プレフィックスまで戻す
        while chunk:
            try:
                chunk.decode('utf-8')
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        if not chunk:
            raise ValueError('ICS fold: 文字境界での分割に失敗しました')
        parts.append(chunk.decode('utf-8'))
        octets = octets[len(chunk):]
        limit = cont_limit
    return '\r\n '.join(parts)


def generate_events_ics_text(events: List[Event], last_changed_at: datetime) -> str:
    """events.ics の内容を生成（RFC 5545 準拠・決定論的出力）

    - 終日イベントとして DTSTART;VALUE=DATE を使用
    - DTEND は最終日の翌日（排他的終了）
    - 開始日が確定していないイベントは含めない
    - DTSTAMP はシート内容が変わった時刻 (last_changed_at) 由来なので実行ごとに変わらない
    """
    dtstamp = last_changed_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//shinichi-ohki//Maker Event Generator//JA',
        'CALSCALE:GREGORIAN',
        'X-WR-CALNAME:' + _escape_ical_text('メイカーイベント一覧 (Upcoming Maker Events)'),
    ]
    for event in events:
        if not event.parsed_date_from:
            continue
        start = event.parsed_date_from
        end = event.parsed_date_to or start
        lines.append('BEGIN:VEVENT')
        lines.append(f'UID:{event_public_id(event.name)}@{ICS_UID_DOMAIN}')
        lines.append(f'DTSTAMP:{dtstamp}')
        lines.append('DTSTART;VALUE=DATE:' + start.strftime('%Y%m%d'))
        lines.append('DTEND;VALUE=DATE:' + (end + timedelta(days=1)).strftime('%Y%m%d'))
        lines.append('SUMMARY:' + _escape_ical_text(event.name))
        if event.url:
            lines.append('URL:' + event.url)
        location_parts = [p for p in (event.venue, event.region, event.country) if p]
        if location_parts:
            lines.append('LOCATION:' + _escape_ical_text(', '.join(location_parts)))
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold_ical_line(line) for line in lines) + '\r\n'


def generate_llms_txt_text(events: List[Event]) -> str:
    """llms.txt（https://llmstxt.org/ 形式）の内容を生成"""
    japan_count = sum(1 for e in events if e.is_japan)
    intl_count = len(events) - japan_count
    return f"""# Upcoming Maker Events（メイカーイベント一覧）

> Google Sheets のデータから毎日自動生成している、日本と海外のメイカー系イベントの静的一覧サイトです。

## リンク

- [イベント一覧ページ]({SITE_URL}): 日本のイベントは日本語、海外のイベントは英語で掲載
- [events.json]({SITE_URL}events.json): 今後のイベントの機械可読データ（JSON / UTF-8 / {len(events)}件: 日本{japan_count}・海外{intl_count}）。日付未確定のイベントは start_date / end_date が null
- [events.ics]({SITE_URL}events.ics): カレンダー購読用 iCalendar（RFC 5545）

## 備考

- 今後開催されるイベントのみを掲載しています（終了したイベントは自動的に除外されます）
- 日付が未確定（例: 「2027年春頃」）のイベントは events.json にのみ含まれ、一覧ページと events.ics には含まれません
- 毎日、日本時間の早朝に自動更新されます
- 日程・会場・出演内容などの最新情報は、各イベントの公式 URL で必ずご確認ください
"""


def generate_html(events: List[Event], template_dir: str = "templates") -> str:
    """HTMLページを生成"""
    
    # テンプレートディレクトリを作成
    Path(template_dir).mkdir(exist_ok=True)
    
    # OGP画像を生成
    print("🖼️ OGP画像を生成中...")
    ogp_image_path = create_ogp_image(events)
    ogp_image_url = f"https://shinichi-ohki.github.io/maker_event/{ogp_image_path}" if ogp_image_path else "https://via.placeholder.com/1200x630/667eea/ffffff?text=Upcoming+Maker+Events"
    
    # デフォルトテンプレートを作成
    template_content = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Upcoming Maker Events | 今後のメイカーイベント</title>
    
    <!-- OGP Meta Tags for Social Media Sharing -->
    <meta property="og:title" content="Upcoming Maker Events | 今後のメイカーイベント">
    <meta property="og:description" content="世界中のメイカーイベント情報を一覧で確認。Maker Faire、NT、技術書典など{{ total_events }}件のイベント情報を掲載。 | Discover upcoming maker events worldwide including Maker Faires, technical conferences, and maker gatherings.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="https://shinichi-ohki.github.io/maker_event/">
    <meta property="og:image" content="{{ ogp_image_url }}">
    <meta property="og:site_name" content="Maker Events">
    <meta property="og:locale" content="ja_JP">
    
    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="Upcoming Maker Events | 今後のメイカーイベント">
    <meta name="twitter:description" content="世界中のメイカーイベント情報を一覧で確認。{{ total_events }}件のイベント情報を掲載。">
    <meta name="twitter:image" content="{{ ogp_image_url }}">
    
    <!-- Standard Meta Tags -->
    <meta name="description" content="世界中のメイカーイベント情報を一覧で確認。Maker Faire、NT、技術書典など{{ total_events }}件のイベント情報を掲載。">
    <meta name="keywords" content="Maker Faire, メイカーイベント, 技術イベント, NT, 技術書典, DIY, ハードウェア, プログラミング">
    <meta name="author" content="Maker Events Team">
    <link rel="alternate" type="application/json" href="events.json">
    <link rel="alternate" type="text/calendar" href="events.ics" title="Upcoming Maker Events (iCalendar)">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            background-color: #f8f9fa;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        
        header {
            text-align: center;
            margin-bottom: 40px;
            padding: 40px 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
        }
        
        h1 {
            font-size: 2.5rem;
            margin-bottom: 10px;
        }
        
        .subtitle {
            font-size: 1.2rem;
            opacity: 0.9;
        }
        
        .events-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 30px;
            margin-top: 40px;
        }
        
        .event-card {
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            overflow: hidden;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }
        
        .event-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 20px 40px rgba(0,0,0,0.15);
        }
        
        .event-image {
            width: 100%;
            height: 200px;
            background: linear-gradient(45deg, #667eea, #764ba2);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 3rem;
        }
        
        .event-content {
            padding: 25px;
        }
        
        .event-date {
            background: #667eea;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 0.9rem;
            font-weight: bold;
            display: inline-block;
            margin-bottom: 15px;
        }
        
        .event-title {
            font-size: 1.4rem;
            font-weight: bold;
            margin-bottom: 10px;
            color: #2c3e50;
        }
        
        .event-location {
            color: #7f8c8d;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
        }
        
        .event-location::before {
            content: "📍";
            margin-right: 8px;
        }
        
        .event-description {
            color: #555;
            font-size: 0.95rem;
            line-height: 1.5;
            margin-bottom: 20px;
        }
        
        .event-link {
            display: inline-block;
            background: #667eea;
            color: white;
            padding: 10px 20px;
            text-decoration: none;
            border-radius: 25px;
            font-weight: bold;
            transition: background 0.3s ease;
        }
        
        .event-link:hover {
            background: #5a67d8;
        }
        
        .no-events {
            text-align: center;
            color: #7f8c8d;
            font-size: 1.2rem;
            margin-top: 60px;
        }
        
        .section-title {
            font-size: 2rem;
            margin: 40px 0 20px 0;
            text-align: center;
            color: #2c3e50;
        }
        
        .japan-events {
            margin-bottom: 60px;
        }
        
        .international-events {
            margin-bottom: 60px;
        }
        
        @media (max-width: 768px) {
            .events-grid {
                grid-template-columns: 1fr;
                gap: 20px;
            }
            
            h1 {
                font-size: 2rem;
            }
            
            .container {
                padding: 10px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Upcoming Maker Events</h1>
            <p class="subtitle">今後のメイカーイベント | Discover maker events worldwide</p>
        </header>
        
        {% if japan_events %}
        <section class="japan-events">
            <h2 class="section-title">🇯🇵 日本のイベント | Events in Japan</h2>
            <div class="events-grid">
                {% for event in japan_events %}
                <div class="event-card">
                    <div class="event-image">
                        {% if event.image_url %}
                            <img src="{{ event.image_url }}" alt="{{ event.name }}" style="width: 100%; height: 100%; object-fit: cover;">
                        {% else %}
                            🛠️
                        {% endif %}
                    </div>
                    <div class="event-content">
                        {% if event.parsed_date_from %}
                        <div class="event-date">{{ format_event_date(event) }}</div>
                        {% endif %}
                        <h3 class="event-title">{{ event.name }}</h3>
                        <p class="event-location">{{ event.location }}{% if event.country and event.country != event.location %}, {{ event.country }}{% endif %}</p>
                        {% if event.description %}
                        <p class="event-description">{{ event.description[:150] }}{% if event.description|length > 150 %}...{% endif %}</p>
                        {% endif %}
                        {% if event.url %}
                        <a href="{{ event.url }}" class="event-link" target="_blank">詳細を見る</a>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}
        
        {% if international_events %}
        <section class="international-events">
            <h2 class="section-title">🌍 International Events | 海外のイベント</h2>
            <div class="events-grid">
                {% for event in international_events %}
                <div class="event-card">
                    <div class="event-image">
                        {% if event.image_url %}
                            <img src="{{ event.image_url }}" alt="{{ event.name }}" style="width: 100%; height: 100%; object-fit: cover;">
                        {% else %}
                            🛠️
                        {% endif %}
                    </div>
                    <div class="event-content">
                        {% if event.parsed_date_from %}
                        <div class="event-date">{{ format_event_date(event) }}</div>
                        {% endif %}
                        <h3 class="event-title">{{ event.name }}</h3>
                        <p class="event-location">{{ event.location }}{% if event.country and event.country != event.location %}, {{ event.country }}{% endif %}</p>
                        {% if event.description %}
                        <p class="event-description">{{ event.description[:150] }}{% if event.description|length > 150 %}...{% endif %}</p>
                        {% endif %}
                        {% if event.url %}
                        <a href="{{ event.url }}" class="event-link" target="_blank">Learn More</a>
                        {% endif %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}
        
        {% if not japan_events and not international_events %}
        <div class="no-events">
            <p>現在、今後のイベント情報はありません。<br>
            No upcoming events are currently scheduled.</p>
        </div>
        {% endif %}
    </div>
    
    <div style="text-align: center; margin: 2rem 0 1rem 0;">
        <h3 style="margin-bottom: 1rem; color: #333; font-size: 1.2rem;">イベントスケジュール | Event Timeline</h3>
        <img src="ogp_image.png" alt="Upcoming Maker Events Timeline" style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
    </div>
    
    <footer style="text-align: center; margin-top: 1rem; padding: 1rem; color: #666; font-size: 0.8rem; border-top: 1px solid #e0e0e0;">
        <p>Last updated: {{ last_updated }}</p>
        <p>Data feeds: <a href="events.json" style="color: #666;">events.json</a> | <a href="events.ics" style="color: #666;">Calendar (iCal)</a></p>
    </footer>
</body>
</html>"""
    
    template_path = Path(template_dir) / "index.html"
    template_path.write_text(template_content, encoding='utf-8')
    
    # イベントを日本と海外に分類
    japan_events = [e for e in events if e.is_japan]
    international_events = [e for e in events if not e.is_japan]
    
    # Jinja2でレンダリング
    env = Environment(loader=FileSystemLoader(template_dir))
    env.globals['format_event_date'] = format_event_date
    template = env.get_template("index.html")
    
    # 現在の日時を取得（日本時間）
    from datetime import timezone, timedelta
    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst)
    last_updated = now_jst.strftime("%Y-%m-%d %H:%M JST")
    
    return template.render(
        japan_events=japan_events,
        international_events=international_events,
        total_events=len(events),
        ogp_image_url=ogp_image_url,
        last_updated=last_updated
    )


def main():
    """メイン処理"""
    # コマンドライン引数の解析
    parser = argparse.ArgumentParser(description='メイカーイベント静的サイト生成スクリプト')
    parser.add_argument('--auto-push', action='store_true', 
                       help='変更があった場合、自動的にGitにコミット・プッシュする')
    parser.add_argument('--force', action='store_true',
                       help='変更検出をスキップして強制的に実行する')
    args = parser.parse_args()
    
    sheet_url = "https://docs.google.com/spreadsheets/d/1a2XqNp01q6hFiyyFjq5hMlYGV66Z9UeOHZP4snSXaz0/edit?gid=0#gid=0"
    
    # ページ更新が必要かチェック（--forceオプションでスキップ可能）
    if not args.force:
        print("🔍 ページ更新の必要性をチェック中...")
        should_update, current_hash = should_update_page(sheet_url)
        
        if not should_update:
            print("⏭️  更新不要のため、HTML生成をスキップします")
            return
    else:
        print("⚡ 強制実行モード: 変更検出をスキップします")
        current_hash = ""
    
    print("🔄 Google Sheetsからデータを取得中...")
    raw_events = fetch_events_from_sheet(sheet_url)
    print(f"✅ {len(raw_events)}件の生データを取得しました")
    
    print("🔄 イベントデータを解析中...")
    events = parse_events(raw_events)
    print(f"✅ {len(events)}件のイベントを解析しました")
    
    print("🔄 今後のイベントをフィルタリング中...")
    upcoming_events = filter_upcoming_events(events)
    print(f"✅ {len(upcoming_events)}件の今後のイベントを抽出しました")

    # 日付未確定イベント（「2027年春頃」等）は events.json にのみ含める
    undated_events = select_undated_events(events)
    if undated_events:
        print(f"🗓️  日付未確定イベント {len(undated_events)}件を events.json に含めます")
    json_events = upcoming_events + undated_events

    # シート内容が実際に変わった時刻を追跡（events.json の updated_at / ICS の DTSTAMP 用）
    # 同じデータなら出力のバイト列が変わらないよう、実行時刻ではなく変更検出時刻を使う
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    prev_state = load_last_state()
    prev_hash = prev_state.get('content_hash', '')
    if (current_hash and current_hash != prev_hash) or 'last_changed_at' not in prev_state:
        last_changed_at = now_jst
    else:
        try:
            last_changed_at = datetime.fromisoformat(prev_state['last_changed_at'])
        except (KeyError, ValueError):
            last_changed_at = now_jst

    print("🔄 HTMLページを生成中...")
    html_content = generate_html(upcoming_events)
    
    output_path = Path("index.html")
    output_path.write_text(html_content, encoding='utf-8')
    print(f"✅ HTMLページを生成しました: {output_path.absolute()}")

    print("🔄 機械可読データ (events.json / events.ics / llms.txt) を生成中...")
    Path("events.json").write_bytes(generate_events_json_text(json_events, last_changed_at).encode('utf-8'))
    Path("events.ics").write_bytes(generate_events_ics_text(json_events, last_changed_at).encode('utf-8'))
    Path("llms.txt").write_bytes(generate_llms_txt_text(json_events).encode('utf-8'))
    print("✅ events.json / events.ics / llms.txt を生成しました")
    
    # 統計情報を表示
    japan_count = len([e for e in upcoming_events if e.is_japan])
    international_count = len([e for e in upcoming_events if not e.is_japan])
    
    print(f"\n📊 統計情報:")
    print(f"   日本のイベント: {japan_count}件")
    print(f"   海外のイベント: {international_count}件")
    print(f"   合計: {len(upcoming_events)}件")
    if undated_events:
        print(f"   日付未確定: {len(undated_events)}件（events.json にのみ掲載）")
    
    # 成功時に現在の状態を保存（--forceモードの場合は現在のハッシュを取得）
    if args.force:
        _, current_hash = has_spreadsheet_changed(sheet_url)
    
    state = {
        'content_hash': current_hash,
        'last_updated': datetime.now().isoformat(),
        'event_count': len(upcoming_events),
        'last_changed_at': last_changed_at.isoformat(),
    }
    save_last_state(state)
    print(f"💾 状態を保存しました: {current_hash[:8]}")
    
    # 自動プッシュオプションが指定されている場合
    if args.auto_push:
        print("\n🔄 Gitリポジトリへの自動プッシュを実行中...")
        success = auto_commit_and_push()
        if success:
            print("✅ 自動プッシュが完了しました")
        else:
            print("⏭️  変更がないためプッシュをスキップしました")


if __name__ == "__main__":
    main()
