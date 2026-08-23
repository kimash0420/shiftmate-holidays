"""공휴일 생성기 테스트.

**이 저장소 안에서 돌린다.** 앱 저장소(AUTOPILOT)의 pytest 게이트는 `testpaths = tests`라
여기를 수집하지 않고, 이 저장소는 GitHub에 단독으로 올라가므로 **혼자서 검증 가능해야**
한다. 워크플로가 공휴일을 다시 만들기 **전에** 이 테스트를 돌린다.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_holidays import (  # noqa: E402
    BuildError,
    build_document,
    check_not_shrinking,
    collect,
    default_years,
    existing_dates,
    source_label,
    write_document,
)


class TestCollect:
    """출처가 `holidays` 패키지 하나뿐이라, 그 출처가 실제로 무엇을 주는지 못박아 둔다."""

    def test_알려진_공휴일이_들어_있다(self):
        dates = {h["date"] for h in collect(2026)}
        assert {"2026-01-01", "2026-03-01", "2026-08-15", "2026-12-25"} <= dates

    def test_대체공휴일을_계산한다(self):
        # 2026-08-15(광복절)이 토요일이라 08-17이 대체공휴일이다.
        dates = {h["date"] for h in collect(2026)}
        assert "2026-08-17" in dates

    def test_임시공휴일도_담는다(self):
        # 라이브러리에 의존하는 이 설계의 가장 큰 약점이 임시공휴일이다.
        # 과거 실제 사례로 출처의 능력을 고정한다.
        assert "2025-01-27" in {h["date"] for h in collect(2025)}
        assert "2023-10-02" in {h["date"] for h in collect(2023)}

    def test_제헌절_재지정을_반영한다(self):
        # 2026-04-28 국무회의에서 18년 만에 공휴일로 되살아났다.
        assert "2026-07-17" in {h["date"] for h in collect(2026)}

    def test_노동절이_들어_있다(self):
        # 사용자 확인(2026-08-23): 노동절에는 당직근무자만 일한다 = 주간은 쉰다.
        # 관공서 공휴일 기준 출처(특일 정보 API)에는 노동절이 없다 — 그래서 이 출처를 골랐다.
        assert "2026-05-01" in {h["date"] for h in collect(2026)}

    def test_해당_연도만_남긴다(self):
        # 설날 전날이 해를 넘어갈 수 있어 앞뒤 해를 함께 계산한다. 새는 것이 없어야 한다.
        assert all(h["date"].startswith("2026-") for h in collect(2026))

    def test_날짜순이고_중복이_없다(self):
        dates = [h["date"] for h in collect(2026)]
        assert dates == sorted(dates)
        assert len(dates) == len(set(dates))

    def test_이름이_비어_있지_않다(self):
        assert all(h["name"].strip() for h in collect(2026))


class TestDocument:
    def test_명세_4_2_스키마에_source를_더한다(self):
        doc = build_document(2026, [{"date": "2026-01-01", "name": "신정"}], date(2026, 8, 23))
        assert doc["year"] == 2026
        assert doc["updated"] == "2026-08-23"
        assert doc["holidays"] == [{"date": "2026-01-01", "name": "신정"}]
        # 앱은 모르는 키를 무시한다. 값이 이상할 때 어느 판이 만들었는지가 유일한 단서다.
        assert doc["source"].startswith("holidays==")

    def test_내용이_같아도_updated는_갱신된다(self):
        # updated는 "마지막으로 성공한 날"이다 — 앱이 이걸로 관리 여부를 판단한다(명세 4.5).
        holiday_list = [{"date": "2026-01-01", "name": "신정"}]
        first = build_document(2026, holiday_list, date(2026, 8, 1))
        second = build_document(2026, holiday_list, date(2026, 9, 1))
        assert first["holidays"] == second["holidays"]
        assert first["updated"] != second["updated"]

    def test_올해와_내년을_받는다(self):
        assert default_years(date(2026, 12, 31)) == [2026, 2027]

    def test_source에_실제_버전이_들어간다(self):
        assert source_label() != "holidays==unknown"

    def test_파일을_원자적으로_쓴다(self, tmp_path):
        doc = build_document(2026, [{"date": "2026-01-01", "name": "신정"}], date(2026, 8, 23))
        path = write_document(doc, tmp_path)

        assert path.name == "2026.json"
        assert json.loads(path.read_text(encoding="utf-8")) == doc
        assert list(tmp_path.glob("*.tmp")) == []

    def test_한글이_이스케이프되지_않는다(self, tmp_path):
        doc = build_document(2026, [{"date": "2026-03-01", "name": "삼일절"}], date(2026, 8, 23))
        text = write_document(doc, tmp_path).read_text(encoding="utf-8")
        assert "삼일절" in text
        assert text.endswith("\n")


class TestShrinkGuard:
    """출처가 외부 라이브러리 하나뿐이라, 그것이 **조용히 나빠지는 것**이 최대 위험이다."""

    def _write(self, tmp_path: Path, dates: list[str]) -> Path:
        doc = build_document(2026, [{"date": d, "name": "x"} for d in dates], date(2026, 8, 1))
        return write_document(doc, tmp_path)

    def test_날짜가_사라지면_멈춘다(self, tmp_path):
        path = self._write(tmp_path, ["2026-01-01", "2026-03-01"])
        with pytest.raises(BuildError, match="사라졌다"):
            check_not_shrinking(2026, {"2026-01-01"}, path)

    def test_늘어나는_것은_통과한다(self, tmp_path):
        path = self._write(tmp_path, ["2026-01-01"])
        check_not_shrinking(2026, {"2026-01-01", "2026-03-01"}, path)

    def test_그대로면_통과한다(self, tmp_path):
        path = self._write(tmp_path, ["2026-01-01"])
        check_not_shrinking(2026, {"2026-01-01"}, path)

    def test_파일이_없으면_비교하지_않는다(self, tmp_path):
        check_not_shrinking(2026, {"2026-01-01"}, tmp_path / "2026.json")

    def test_파일이_깨져_있으면_비교하지_않는다(self, tmp_path):
        broken = tmp_path / "2026.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert existing_dates(broken) is None
        check_not_shrinking(2026, {"2026-01-01"}, broken)

    def test_사라진_날짜를_메시지에_적는다(self, tmp_path):
        # 무엇이 사라졌는지 없이 "줄었다"만 있으면 사람이 판단할 수 없다.
        path = self._write(tmp_path, ["2026-01-01", "2026-05-01"])
        with pytest.raises(BuildError, match="2026-05-01"):
            check_not_shrinking(2026, {"2026-01-01"}, path)
