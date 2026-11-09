"""地域列からの国判定 (extract_country_from_region) のテスト"""
from generate_events import extract_country_from_region, load_country_mapping

MAPPING = load_country_mapping()


def test_prefecture_in_parens_is_japan():
    assert extract_country_from_region("武蔵小杉(神奈川)", MAPPING) == "Japan"
    assert extract_country_from_region("函館(北海道)", MAPPING) == "Japan"
    assert extract_country_from_region("長岡(新潟)", MAPPING) == "Japan"


def test_country_in_parens():
    assert extract_country_from_region("パリ(フランス)", MAPPING) == "France"
    assert extract_country_from_region("サンフランシスコ(アメリカ)", MAPPING) == "USA"
    assert extract_country_from_region("バンコク(タイ)", MAPPING) == "Thailand"
    assert extract_country_from_region("深圳(中国)", MAPPING) == "China"
    assert extract_country_from_region("Kuching(マレーシア)", MAPPING) == "Malaysia"


def test_country_outside_parens():
    # 括弧の位置が逆（国名(都市名)）でも外側の国名で判定される
    assert extract_country_from_region("タイ(バンコク)", MAPPING) == "Thailand"


def test_mongolia_is_mapped():
    assert extract_country_from_region("ウランバートル(モンゴル)", MAPPING) == "Mongolia"


def test_no_parens():
    assert extract_country_from_region("東京", MAPPING) == "Japan"
    assert extract_country_from_region("名古屋", MAPPING) == "Japan"
    # 括弧なしでも地域全体が国名ならそれを使用
    assert extract_country_from_region("タイ", MAPPING) == "Thailand"


def test_fullwidth_parens():
    assert extract_country_from_region("パリ（フランス）", MAPPING) == "France"


def test_unknown_paren_content_returns_raw():
    # 判定不能な場合は日本扱いにせず、入力をそのまま返して問題を見える化する
    assert extract_country_from_region("どこか(謎の国)", MAPPING) == "謎の国"
