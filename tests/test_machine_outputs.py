"""events.json / events.ics / llms.txt 生成機能のテスト"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from generate_events import (
    Event,
    event_public_id,
    generate_events_ics_text,
    generate_events_json_text,
    generate_llms_txt_text,
    parse_events,
    select_undated_events,
)

# 決定論的出力の検証に使う固定の last_changed_at（JST）
STAMP = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone(timedelta(hours=9)))


def ev(name, **kw):
    """テスト用 Event を作るヘルパー"""
    params = dict(
        location="テスト会場, 東京",
        country="Japan",
        url="https://example.com",
        venue="テスト会場",
        region="東京",
        date_text="05/01",
        date_from="2026/05/01",
        date_to="2026/05/01",
    )
    params.update(kw)
    return Event(name=name, **params)


EVENTS = [
    ev("単日イベント"),
    ev("複数日イベント", date_from="2026/05/10", date_to="2026/05/12", date_text="05/10-05/12"),
    ev("日付未確定", date_from="", date_to="", date_text="2027年春頃"),
    ev("予定イベント (予定)", date_from="2026/06/01", date_to="2026/06/02",
       date_text="06/01-06/02", x_url="https://x.com/example", is_tentative=True),
    ev("Escaping, test; \\ event", date_from="2026/07/01", date_to="",
       date_text="07/01", venue="Hall, A; B"),
]


# ---------- events.json ----------

def test_json_event_count_matches_input():
    data = json.loads(generate_events_json_text(EVENTS, STAMP))
    assert len(data["events"]) == len(EVENTS)


def test_json_schema_fields_and_date_formats():
    data = json.loads(generate_events_json_text(EVENTS, STAMP))
    assert data["schema_version"] == 1
    assert data["site"] == "https://shinichi-ohki.github.io/maker_event/"
    assert data["updated_at"] == "2026-11-09T12:00:00+09:00"

    required = {"id", "name", "start_date", "end_date", "date_text", "is_tentative",
                "country", "region", "venue", "url", "x_url"}
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    ids = set()
    for e in data["events"]:
        assert required <= set(e.keys()), f"必須キー不足: {e}"
        for key in ("start_date", "end_date"):
            assert e[key] is None or date_re.match(e[key]), f"日付形式不正: {e[key]}"
        assert isinstance(e["is_tentative"], bool)
        assert e["name"] and e["country"]
        assert e["id"] not in ids, f"id 重複: {e['id']}"
        ids.add(e["id"])

    by_name = {e["name"]: e for e in data["events"]}
    assert by_name["単日イベント"]["start_date"] == "2026-05-01"
    assert by_name["複数日イベント"]["start_date"] == "2026-05-10"
    assert by_name["複数日イベント"]["end_date"] == "2026-05-12"
    assert by_name["日付未確定"]["start_date"] is None
    assert by_name["日付未確定"]["end_date"] is None
    assert by_name["日付未確定"]["date_text"] == "2027年春頃"
    assert by_name["予定イベント (予定)"]["is_tentative"] is True
    assert by_name["予定イベント (予定)"]["x_url"] == "https://x.com/example"
    assert by_name["単日イベント"]["x_url"] is None


def test_event_id_is_name_based_and_normalized():
    # 名前ベース（日付変更で変わらない）+ 前後空白の正規化
    assert event_public_id("Coolest Projects Japan 2026 ") == event_public_id("Coolest Projects Japan 2026")
    assert event_public_id("同じイベント") == event_public_id("同じイベント")
    assert event_public_id("別のイベント") != event_public_id("同じイベント")


# ---------- parse_events の新規フィールド ----------

def test_parse_events_reads_x_column_and_tentative():
    rows = [
        {"名称": "NT金沢 2027(予定)", "地域": "金沢", "場所": "金沢駅東もてなしドーム地下広場",
         "から": "2027/06/26", "まで": "2027/06/27", "URL": "", "X": "", "備考": ""},
        {"名称": "通常イベント", "地域": "東京", "場所": "会場",
         "から": "05/01", "まで": "05/01", "URL": "https://example.com", "X": "https://x.com/foo", "備考": ""},
    ]
    events = parse_events(rows)
    assert len(events) == 2
    assert events[0].is_tentative is True
    assert events[0].x_url == ""
    assert events[0].date_text == "2027/06/26-2027/06/27"
    assert events[0].venue == "金沢駅東もてなしドーム地下広場"
    assert events[0].region == "金沢"
    assert events[1].is_tentative is False
    assert events[1].x_url == "https://x.com/foo"
    assert events[1].date_text == "05/01"


# ---------- events.ics ----------

def test_ics_dtend_is_exclusive():
    ics = generate_events_ics_text(EVENTS, STAMP)
    # 複数日: 05/10-05/12 → DTEND は 05/13
    assert "DTSTART;VALUE=DATE:20260510" in ics
    assert "DTEND;VALUE=DATE:20260513" in ics
    # 単日: 05/01 → DTEND は 05/02
    assert "DTSTART;VALUE=DATE:20260501" in ics
    assert "DTEND;VALUE=DATE:20260502" in ics


def test_ics_excludes_undefined_date_events():
    ics = generate_events_ics_text(EVENTS, STAMP)
    assert "日付未確定" not in ics
    assert ics.count("BEGIN:VEVENT") == len(EVENTS) - 1  # 未確定を除く


def test_ics_uid_matches_json_id():
    data = json.loads(generate_events_json_text(EVENTS, STAMP))
    ics = generate_events_ics_text(EVENTS, STAMP)
    for e in data["events"]:
        if e["start_date"]:
            assert f"UID:{e['id']}@shinichi-ohki.github.io" in ics


def test_ics_crlf_and_folding():
    ics = generate_events_ics_text(EVENTS, STAMP)
    assert ics.endswith("\r\n")
    # 裸 LF (\r を伴わない \n) が無いこと
    assert "\n" not in ics.replace("\r\n", "")
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"75オクテット超の行: {line!r}"


def test_ics_dtstamp_deterministic_and_utc():
    ics = generate_events_ics_text(EVENTS, STAMP)
    # JST 12:00 = UTC 03:00
    assert "DTSTAMP:20261109T030000Z" in ics


def test_ics_parseable_by_icalendar():
    icalendar = pytest.importorskip("icalendar")  # 別実装のパーサー
    parse_ics = getattr(icalendar, "from_ical", None) or icalendar.Calendar.from_ical
    ics = generate_events_ics_text(EVENTS, STAMP)
    cal = parse_ics(ics)
    vevents = list(cal.walk("VEVENT"))
    assert len(vevents) == len(EVENTS) - 1
    names = {str(v["SUMMARY"]) for v in vevents}
    # TEXT エスケープが往復できること
    assert "Escaping, test; \\ event" in names
    assert "単日イベント" in names
    assert str(cal["X-WR-CALNAME"]).startswith("メイカーイベント一覧")
    with_url = [v for v in vevents if "URL" in v]
    assert with_url and str(with_url[0]["URL"]).startswith("https://")
    with_loc = [v for v in vevents if "LOCATION" in v]
    assert with_loc and "テスト会場" in str(with_loc[0]["LOCATION"])


# ---------- 日付未確定イベントの合成（main() と同じ構成） ----------

def test_select_undated_events():
    undated = select_undated_events(EVENTS)
    assert [e.name for e in undated] == ["日付未確定"]


def test_json_includes_undated_but_ics_excludes():
    # main() と同じ合成: 日付確定済み + 未確定（シート順）
    dated = sorted([e for e in EVENTS if e.parsed_date is not None],
                   key=lambda x: x.parsed_date)
    combined = dated + select_undated_events(EVENTS)

    data = json.loads(generate_events_json_text(combined, STAMP))
    assert len(data["events"]) == len(EVENTS)
    last = data["events"][-1]
    assert last["name"] == "日付未確定"
    assert last["start_date"] is None and last["end_date"] is None
    assert last["date_text"] == "2027年春頃"

    ics = generate_events_ics_text(combined, STAMP)
    assert "日付未確定" not in ics
    assert ics.count("BEGIN:VEVENT") == len(dated)


# ---------- llms.txt ----------

def test_llms_txt_format_and_links():
    text = generate_llms_txt_text(EVENTS)
    assert text.startswith("# ")
    assert "\n> " in text  # blockquote 要約
    assert "https://shinichi-ohki.github.io/maker_event/events.json" in text
    assert "https://shinichi-ohki.github.io/maker_event/events.ics" in text
    assert "https://shinichi-ohki.github.io/maker_event/" in text
    assert "今後開催されるイベントのみ" in text
    assert "公式 URL" in text
    assert "日付が未確定" in text  # 未確定イベントの説明


# ---------- 決定論性 ----------

def test_outputs_are_deterministic():
    assert generate_events_json_text(EVENTS, STAMP) == generate_events_json_text(EVENTS, STAMP)
    assert generate_events_ics_text(EVENTS, STAMP) == generate_events_ics_text(EVENTS, STAMP)
    assert generate_llms_txt_text(EVENTS) == generate_llms_txt_text(EVENTS)
