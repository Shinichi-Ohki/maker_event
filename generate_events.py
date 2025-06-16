#!/usr/bin/env python3
"""
メイカーイベント静的サイト生成スクリプト
Maker Event Static Site Generator
"""

import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin
import io
import zipfile

import requests
from bs4 import BeautifulSoup
from dateutil import parser
from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field


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
    
    def model_post_init(self, __context):
        """初期化後処理"""
        if self.date:
            try:
                self.parsed_date = parser.parse(self.date)
            except:
                self.parsed_date = None
        
        self.is_japan = self.country.lower() in ['japan', '日本', 'jp']


def get_spreadsheet_csv_url(sheet_url: str) -> str:
    """Google SheetsのURLをCSVエクスポート用URLに変換"""
    if 'docs.google.com/spreadsheets' in sheet_url:
        sheet_id = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', sheet_url)
        if sheet_id:
            return f"https://docs.google.com/spreadsheets/d/{sheet_id.group(1)}/export?format=csv"
    return sheet_url


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
        if og_image and og_image.get('content'):
            image_url = og_image['content']
            # 相対URLの場合は絶対URLに変換
            if image_url.startswith('/'):
                image_url = urljoin(url, image_url)
            return image_url
        
        # Twitter Card画像を試す
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            image_url = twitter_image['content']
            if image_url.startswith('/'):
                image_url = urljoin(url, image_url)
            return image_url
        
        # ファビコンを最後の手段として取得
        favicon = soup.find('link', rel='icon') or soup.find('link', rel='shortcut icon')
        if favicon and favicon.get('href'):
            favicon_url = favicon['href']
            if favicon_url.startswith('/'):
                favicon_url = urljoin(url, favicon_url)
            return favicon_url
        
        return ""
        
    except Exception as e:
        print(f"画像取得エラー ({url}): {e}")
        return ""


def download_noto_font() -> str:
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
                    
                    # イベントバーを描画（円形のドット）
                    dot_size = 8
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
                    date_text = event.parsed_date.strftime('%m/%d')
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
    
    for raw in raw_events:
        try:
            # 実際のスプレッドシート列名に基づくマッピング
            name = raw.get('名称', '').strip()
            location = raw.get('場所', '').strip()
            region = raw.get('地域', '').strip()
            date_from = raw.get('から', '').strip()
            date_to = raw.get('まで', '').strip()
            url = raw.get('URL', '').strip()
            description = raw.get('備考', '').strip()
            
            # 空のデータや年だけのヘッダー行をスキップ
            if not name or not location or name.endswith('年'):
                continue
            
            # 日付の組み立て（年を追加）
            current_year = "2025"
            date_str = ""
            if date_from:
                if date_to and date_from != date_to:
                    date_str = f"{current_year}/{date_from}"
                else:
                    date_str = f"{current_year}/{date_from}"
            
            # locationとregionを組み合わせ
            full_location = f"{location}, {region}" if region else location
            
            # 国を判定（海外の都市名が含まれている場合は適切に分類）
            country = '日本'
            if any(keyword in full_location.lower() for keyword in ['san francisco', 'サンフランシスコ', 'bacolod', 'フィリピン', 'seoul', 'ソウル', '韓国', 'rome', 'ローマ', 'shanghai', '上海', '中国', 'shenzhen', '深圳', 'taipei', '台北', '台湾']):
                if 'san francisco' in full_location.lower() or 'サンフランシスコ' in full_location:
                    country = 'USA'
                elif 'bacolod' in full_location.lower() or 'フィリピン' in full_location:
                    country = 'Philippines'
                elif 'seoul' in full_location.lower() or 'ソウル' in full_location or '韓国' in full_location:
                    country = 'South Korea'
                elif 'rome' in full_location.lower() or 'ローマ' in full_location:
                    country = 'Italy'
                elif 'shanghai' in full_location.lower() or '上海' in full_location or '中国' in full_location:
                    country = 'China'
                elif 'shenzhen' in full_location.lower() or '深圳' in full_location:
                    country = 'China'
                elif 'taipei' in full_location.lower() or '台北' in full_location or '台湾' in full_location:
                    country = 'Taiwan'
            
            # 画像URLは後で今後のイベントのみに対して取得する
            image_url = ""
            
            event_data = {
                'name': name,
                'date': date_str,
                'location': full_location,
                'country': country,
                'description': description,
                'url': url,
                'image_url': image_url
            }
            
            if event_data['name'] and event_data['location']:
                event = Event(**event_data)
                events.append(event)
                
        except Exception as e:
            print(f"Error parsing event: {e}")
            continue
    
    return events


def filter_upcoming_events(events: List[Event], days_ahead: int = 365) -> List[Event]:
    """今後開催予定のイベントをフィルタリング"""
    now = datetime.now()
    cutoff_date = now + timedelta(days=days_ahead)
    
    upcoming = []
    for event in events:
        if event.parsed_date and event.parsed_date >= now and event.parsed_date <= cutoff_date:
            upcoming.append(event)
    
    # 今後のイベントのみサムネイルを取得
    for event in upcoming:
        if event.url and not event.image_url:
            print(f"🖼️  画像取得中: {event.name}")
            event.image_url = extract_image_from_url(event.url)
            time.sleep(0.5)  # レート制限を避けるための待機
    
    return sorted(upcoming, key=lambda x: x.parsed_date or datetime.max)


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
                        {% if event.parsed_date %}
                        <div class="event-date">{{ event.parsed_date.strftime('%Y年%m月%d日') }}</div>
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
                        {% if event.parsed_date %}
                        <div class="event-date">{{ event.parsed_date.strftime('%B %d, %Y') }}</div>
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
</body>
</html>"""
    
    template_path = Path(template_dir) / "index.html"
    template_path.write_text(template_content, encoding='utf-8')
    
    # イベントを日本と海外に分類
    japan_events = [e for e in events if e.is_japan]
    international_events = [e for e in events if not e.is_japan]
    
    # Jinja2でレンダリング
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("index.html")
    
    return template.render(
        japan_events=japan_events,
        international_events=international_events,
        total_events=len(events),
        ogp_image_url=ogp_image_url
    )


def main():
    """メイン処理"""
    sheet_url = "https://docs.google.com/spreadsheets/d/1a2XqNp01q6hFiyyFjq5hMlYGV66Z9UeOHZP4snSXaz0/edit?gid=0#gid=0"
    
    print("🔄 Google Sheetsからデータを取得中...")
    raw_events = fetch_events_from_sheet(sheet_url)
    print(f"✅ {len(raw_events)}件の生データを取得しました")
    
    print("🔄 イベントデータを解析中...")
    events = parse_events(raw_events)
    print(f"✅ {len(events)}件のイベントを解析しました")
    
    print("🔄 今後のイベントをフィルタリング中...")
    upcoming_events = filter_upcoming_events(events)
    print(f"✅ {len(upcoming_events)}件の今後のイベントを抽出しました")
    
    print("🔄 HTMLページを生成中...")
    html_content = generate_html(upcoming_events)
    
    output_path = Path("index.html")
    output_path.write_text(html_content, encoding='utf-8')
    print(f"✅ HTMLページを生成しました: {output_path.absolute()}")
    
    # 統計情報を表示
    japan_count = len([e for e in upcoming_events if e.is_japan])
    international_count = len([e for e in upcoming_events if not e.is_japan])
    
    print(f"\n📊 統計情報:")
    print(f"   日本のイベント: {japan_count}件")
    print(f"   海外のイベント: {international_count}件")
    print(f"   合計: {len(upcoming_events)}件")


if __name__ == "__main__":
    main()