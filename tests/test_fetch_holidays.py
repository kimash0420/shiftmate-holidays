"""공휴일 생성기 테스트.

**이 저장소 안에서 돌린다.** 앱 저장소(AUTOPILOT)의 pytest 게이트는 `testpaths = tests`라
여기를 수집하지 않고, 이 저장소는 GitHub에 단독으로 올라가므로 **혼자서 검증 가능해야**
한다. 워크플로가 공휴일을 받아오기 **전에** 이 테스트를 돌린다.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_holidays import (  # noqa: E402
    FetchError,
    build_document,
    default_years,
    normalize_service_key,
    parse_items,
    to_holidays,
    write_document,
)


def envelope(items, result_code="00"):
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": "OK"},
            "body": {"items": items},
        }
    }


def item(locdate, name, is_holiday="Y"):
    return {"locdate": locdate, "dateName": name, "isHoliday": is_holiday}


class TestParseItems:
    """이 API는 항목 수에 따라 모양이 바뀐다. 경계에서 전부 흡수해야 한다."""

    def test_여러_건이면_리스트다(self):
        payload = envelope({"item": [item(20260101, "1월 1일"), item(20260301, "삼일절")]})
        assert len(parse_items(payload)) == 2

    def test_한_건이면_딕셔너리로_온다(self):
        # 리스트를 가정한 구현은 여기서 글자 단위로 순회하거나 터진다.
        payload = envelope({"item": item(20260101, "1월 1일")})
        assert parse_items(payload) == [item(20260101, "1월 1일")]

    def test_없으면_빈_문자열로_온다(self):
        assert parse_items(envelope("")) == []

    def test_items가_없어도_견딘다(self):
        assert parse_items({"response": {"header": {"resultCode": "00"}, "body": {}}}) == []

    def test_NODATA는_정상이다(self):
        # 공휴일이 하나도 없는 달이 있다. 실패로 취급하면 그 해가 통째로 실패한다.
        assert parse_items(envelope("", result_code="03")) == []

    def test_실패_코드는_던진다(self):
        with pytest.raises(FetchError, match="resultCode"):
            parse_items(envelope("", result_code="30"))

    def test_응답_모양이_다르면_던진다(self):
        with pytest.raises(FetchError):
            parse_items("<OpenAPI_ServiceResponse>...")
        with pytest.raises(FetchError, match="response"):
            parse_items({"nope": 1})


class TestToHolidays:
    def test_공휴일이_아닌_기념일은_버린다(self):
        # 이 API는 식목일처럼 쉬지 않는 날도 함께 준다. 넣으면 근무일이 휴무가 된다.
        items = [item(20260101, "1월 1일"), item(20260405, "식목일", is_holiday="N")]
        assert to_holidays(items) == [{"date": "2026-01-01", "name": "1월 1일"}]

    def test_날짜순으로_정렬한다(self):
        items = [item(20260301, "삼일절"), item(20260101, "1월 1일")]
        assert [h["date"] for h in to_holidays(items)] == ["2026-01-01", "2026-03-01"]

    def test_같은_날짜가_두_번_오면_한_번만_남는다(self):
        items = [item(20260218, "설날"), item(20260218, "대체공휴일")]
        result = to_holidays(items)
        assert len(result) == 1
        assert result[0]["date"] == "2026-02-18"

    def test_ISO_날짜로_바꾼다(self):
        assert to_holidays([item(20261225, "기독탄신일")])[0]["date"] == "2026-12-25"

    def test_locdate가_깨지면_던진다(self):
        with pytest.raises(FetchError, match="locdate"):
            to_holidays([item("2026-01-01", "1월 1일")])

    def test_이름이_비면_던진다(self):
        with pytest.raises(FetchError, match="dateName"):
            to_holidays([item(20260101, "   ")])

    def test_isHoliday_대소문자를_가리지_않는다(self):
        assert len(to_holidays([item(20260101, "1월 1일", is_holiday="y")])) == 1


class TestDocument:
    def test_명세_4_2_스키마를_그대로_쓴다(self):
        doc = build_document(2026, [{"date": "2026-01-01", "name": "1월 1일"}], date(2026, 8, 23))
        assert doc == {
            "year": 2026,
            "updated": "2026-08-23",
            "holidays": [{"date": "2026-01-01", "name": "1월 1일"}],
        }

    def test_내용이_같아도_updated는_갱신된다(self):
        # updated는 "마지막으로 성공적으로 조회한 날"이다 — 앱이 이걸로 개발자가 아직
        # 관리 중인지 판단한다(명세 4.5). 내용 변화와 묶으면 정상이 방치로 보인다.
        holidays = [{"date": "2026-01-01", "name": "1월 1일"}]
        first = build_document(2026, holidays, date(2026, 8, 1))
        second = build_document(2026, holidays, date(2026, 9, 1))
        assert first["holidays"] == second["holidays"]
        assert first["updated"] != second["updated"]

    def test_올해와_내년을_받는다(self):
        # 연말에 다음 해 알람이 틀리는 것을 막는다(명세 4.4).
        assert default_years(date(2026, 12, 31)) == [2026, 2027]

    def test_파일을_원자적으로_쓴다(self, tmp_path):
        doc = build_document(2026, [{"date": "2026-01-01", "name": "1월 1일"}], date(2026, 8, 23))
        path = write_document(doc, tmp_path)

        assert path.name == "2026.json"
        assert json.loads(path.read_text(encoding="utf-8")) == doc
        # 임시 파일이 남으면 다음 실행이 그것을 보고 혼란스러워진다.
        assert list(tmp_path.glob("*.tmp")) == []

    def test_한글이_이스케이프되지_않는다(self, tmp_path):
        doc = build_document(2026, [{"date": "2026-03-01", "name": "삼일절"}], date(2026, 8, 23))
        text = write_document(doc, tmp_path).read_text(encoding="utf-8")
        assert "삼일절" in text
        assert text.endswith("\n")


class TestServiceKey:
    def test_인코딩된_키는_디코딩한다(self):
        # requests가 params를 다시 인코딩하므로 %2B가 %252B가 되어 키가 등록되지
        # 않았다는 엉뚱한 오류가 난다.
        assert normalize_service_key("abc%2Bdef%3D%3D") == "abc+def=="

    def test_원본_키는_그대로_둔다(self):
        assert normalize_service_key("abc+def==") == "abc+def=="

    def test_퍼센트가_없으면_건드리지_않는다(self):
        assert normalize_service_key("plainkey123") == "plainkey123"
