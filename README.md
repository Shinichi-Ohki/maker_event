# Maker Event Generator | メイカーイベント生成ツール

A Python script to generate static web pages for maker events from Google Sheets data.

Google Sheetsのデータからメイカーイベントの静的ウェブページを生成するPythonスクリプトです。

## Features | 機能

- 📊 **Google Sheets Integration** - Fetch event data directly from Google Sheets
- 🌍 **Multi-language Support** - Japanese events in Japanese, international events in English
- 📱 **Responsive Design** - Mobile-friendly event listing page
- 🎨 **Modern UI** - Clean, card-based layout inspired by MakerFaire.com
- 📅 **Smart Filtering** - Only shows upcoming events
- 🔄 **Easy Updates** - Simple script execution to refresh the page

---

- 📊 **Google Sheets連携** - Google Sheetsから直接イベントデータを取得
- 🌍 **多言語対応** - 日本のイベントは日本語、海外イベントは英語で表示
- 📱 **レスポンシブデザイン** - モバイルフレンドリーなイベント一覧ページ
- 🎨 **モダンUI** - MakerFaire.comにインスパイアされた洗練されたカードベースレイアウト
- 📅 **スマートフィルタリング** - 今後開催されるイベントのみ表示
- 🔄 **簡単更新** - スクリプト実行でページを簡単に更新

## Requirements | 必要環境

- Python 3.8+
- uv (Python package manager)

**⚠️ Important Note about Font Files | フォントファイルについての重要な注意**

The Noto Sans JP font file (`NotoSansJP-Regular.ttf`) is not included in this repository due to licensing considerations. The script will automatically download the font from Google Fonts when first run. If the download fails, the script will fall back to system default fonts.

Noto Sans JPフォントファイル（`NotoSansJP-Regular.ttf`）はライセンスの関係でリポジトリに含まれていません。スクリプトの初回実行時にGoogle Fontsから自動的にダウンロードされます。ダウンロードに失敗した場合は、システムのデフォルトフォントが使用されます。

## Installation | インストール

1. Clone this repository | このリポジトリをクローン:
```bash
git clone <repository-url>
cd maker_event
```

2. Install dependencies using uv | uvを使って依存関係をインストール:
```bash
uv sync
```

## Usage | 使用方法

### Generate the event page | イベントページを生成:

```bash
uv run generate_events.py
```

The script will:
1. Fetch data from the configured Google Sheets
2. Parse and filter upcoming events
3. Generate a responsive HTML page (`index.html`)
4. Display statistics about the generated events

スクリプトは以下の処理を行います：
1. 設定されたGoogle Sheetsからデータを取得
2. 今後のイベントを解析・フィルタリング
3. レスポンシブなHTMLページ（`index.html`）を生成
4. 生成されたイベントの統計情報を表示

### Output | 出力

The generated `index.html` file will contain:
- **Japanese Events Section** - Events in Japan displayed in Japanese
- **International Events Section** - Events outside Japan displayed in English
- **Responsive Grid Layout** - Cards with event details, dates, and links
- **Modern Styling** - Professional appearance with hover effects

生成される`index.html`ファイルには以下が含まれます：
- **日本のイベントセクション** - 日本のイベントを日本語で表示
- **海外イベントセクション** - 海外のイベントを英語で表示
- **レスポンシブグリッドレイアウト** - イベント詳細、日付、リンクを含むカード
- **モダンスタイリング** - ホバーエフェクト付きのプロフェッショナルな外観

## Configuration | 設定

### Google Sheets URL | Google Sheets URL

The script is configured to use a specific Google Sheets URL. To use your own sheet:

1. Make your Google Sheet publicly viewable
2. Update the `sheet_url` variable in `generate_events.py`
3. Ensure your sheet has the expected column names

スクリプトは特定のGoogle Sheets URLを使用するよう設定されています。独自のシートを使用する場合：

1. Google Sheetを公開表示可能にする
2. `generate_events.py`の`sheet_url`変数を更新
3. シートに期待される列名があることを確認

### Expected Sheet Columns | 期待されるシート列

The script looks for these column names (supports both Japanese and English):

| Japanese | English | Description |
|----------|---------|-------------|
| イベント名 | Event Name | Event title |
| 開催日 | Date | Event date |
| 場所 | Location | Event location |
| 国 | Country | Country name |
| 詳細 | Description | Event description |
| URL | Website | Event website |
| 画像URL | Image URL | Event image |

## Development | 開発

### Install development dependencies | 開発依存関係をインストール:

```bash
uv sync --extra dev
```

### Code formatting | コードフォーマット:

```bash
uv run black .
uv run isort .
```

### Linting | リンティング:

```bash
uv run flake8 .
```

## Project Structure | プロジェクト構造

```
maker_event/
├── generate_events.py      # Main script | メインスクリプト
├── pyproject.toml         # Project configuration | プロジェクト設定
├── README.md              # This file | このファイル
├── templates/             # HTML templates (auto-generated) | HTMLテンプレート（自動生成）
├── index.html            # Generated output | 生成された出力
├── ogp_image.png         # Generated OGP image | 生成されたOGP画像
└── NotoSansJP-Regular.ttf # Font file (auto-downloaded) | フォントファイル（自動ダウンロード）
```

**Note:** Files marked as "auto-generated" or "auto-downloaded" are created when you run the script and are not included in the repository.

**注意:** "自動生成" や "自動ダウンロード" と記載されたファイルは、スクリプト実行時に作成されるファイルで、リポジトリには含まれていません。

## License | ライセンス

This project is open source and available under the MIT License.

このプロジェクトはオープンソースで、MITライセンスの下で利用可能です。

## Contributing | 貢献

Contributions are welcome! Please feel free to submit issues and pull requests.

貢献を歓迎します！イシューやプルリクエストをお気軽に提出してください。