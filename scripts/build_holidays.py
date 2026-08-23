#!/usr/bin/env python3
"""`holidays` 패키지로 연도별 공휴일 JSON을 만든다.

**네트워크도 API 키도 쓰지 않는다.** 원래 공공데이터포털 특일 정보 API로 짰다가
2026-08-23에 갈아탔다 — 그 키는 활용기간이 24개월이라 만료되면 파이프라인이 죽고,
그 시점은 앱이 배포된 지 한참 뒤다. 키가 없으면 만료도 없다.

대신 임시공휴일 반영이 PyPI 릴리스 시차에 종속된다. 그래서 워크플로가 매 실행마다
`holidays`를 최신으로 올리고(재현성보다 신선함이 중요한 자리다), 아래 축소 감지가
"출처가 조용히 나빠지는" 경우를 잡는다.

사용:
    python scripts/build_holidays.py            # 올해 + 내년
    python scripts/build_holidays.py 2026 2027  # 연도 지정
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import holidays

KST = timezone(timedelta(hours=9))

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "holidays"


class BuildError(RuntimeError):
    """만든 결과를 신뢰할 수 없다. **덮어쓰지 않기 위해** 던진다."""


def source_label() -> str:
    """어느 판이 이 파일을 만들었는지 남긴다. 값이 이상할 때 판올림을 되짚는 유일한 단서다."""
    try:
        return f"holidays=={version('holidays')}"
    except PackageNotFoundError:  # 소스 트리에서 직접 돌린 경우
        return "holidays==unknown"


def collect(year: int) -> list[dict[str, str]]:
    """[year]의 공휴일을 `{date, name}` 목록으로. 날짜순, 중복 없음.

    앞뒤 해를 함께 계산하고 [year]만 남기는 이유는 **설날 전날이 해를 넘어갈 수 있기**
    때문이다. 그 해만 계산하면 12월 31일에 걸린 연휴가 통째로 빠진다.
    """
    calendar = holidays.SouthKorea(years=(year - 1, year, year + 1))
    named = {
        day.isoformat(): str(name)
        for day, name in calendar.items()
        if day.year == year
    }
    return [{"date": d, "name": named[d]} for d in sorted(named)]


def build_document(year: int, holiday_list: list[dict[str, str]], updated: date) -> dict:
    """명세 4.2의 스키마 + `source`.

    `updated`는 **마지막으로 성공적으로 만든 날**이다. 목록이 그대로여도 갱신한다 —
    앱은 이 값으로 "개발자가 아직 관리하고 있는가"를 판단해 30일 넘게 멈추면 경고
    배너를 띄운다(명세 4.5). 내용 변화와 묶으면 **정상 동작이 방치로 보인다.**
    반대로 만들기에 실패하면 아무것도 쓰지 않는다(그래야 날짜가 멈추고 경고가 뜬다).

    `source`는 명세 4.2에 없는 추가 필드다. 앱은 모르는 키를 무시하므로 호환이 깨지지
    않고, 값이 이상할 때 **어느 라이브러리 판이 만들었는지**가 유일한 단서가 된다.
    """
    return {
        "year": year,
        "updated": updated.isoformat(),
        "source": source_label(),
        "holidays": holiday_list,
    }


def existing_dates(path: Path) -> set[str] | None:
    """이미 있는 파일의 날짜 집합. 없거나 읽을 수 없으면 None(비교를 건너뛴다)."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {h["date"] for h in loaded["holidays"]}
    except (ValueError, KeyError, TypeError):
        return None


def check_not_shrinking(year: int, new_dates: set[str], path: Path) -> None:
    """전에 있던 날짜가 사라졌으면 멈춘다.

    이 저장소의 유일한 출처가 외부 라이브러리이므로 **출처가 조용히 나빠지는 것**이
    가장 큰 위험이다. 사라진 날짜는 라이브러리가 오류를 고친 것일 수도 있고 회귀일
    수도 있는데, 그 판단은 사람이 해야 한다. 워크플로를 실패시켜 눈에 띄게 만든다.
    """
    previous = existing_dates(path)
    if previous is None:
        return
    removed = sorted(previous - new_dates)
    if removed:
        raise BuildError(
            f"{year}년: 전에 있던 공휴일 {len(removed)}건이 사라졌다 — {removed}. "
            f"라이브러리가 고친 것인지 회귀인지 확인하고, 맞다면 --allow-shrink로 진행할 것."
        )


def write_document(document: dict, output_dir: Path = OUTPUT_DIR) -> Path:
    """원자적으로 쓴다 — 중간에 죽어 반쯤 쓰인 JSON이 남으면 앱이 그것을 받아간다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{document['year']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def default_years(today: date) -> list[int]:
    """올해와 내년. 연말에 다음 해 알람이 틀리는 것을 막는다(명세 4.4)."""
    return [today.year, today.year + 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("years", nargs="*", type=int, help="생성할 연도 (기본: 올해·내년)")
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR, help="JSON을 쓸 디렉토리"
    )
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="공휴일이 줄어드는 것을 허용한다(사람이 확인한 뒤에만)",
    )
    args = parser.parse_args(argv)

    today = datetime.now(KST).date()
    years = args.years or default_years(today)

    for year in years:
        holiday_list = collect(year)
        if not holiday_list:
            # 한 해에 공휴일이 0건일 수는 없다. 조용히 빈 파일을 쓰면 그날부터
            # 모든 사용자의 휴무 판정이 사라진다.
            raise BuildError(f"{year}년 공휴일이 0건이다 — 결과를 신뢰할 수 없다")

        path = args.output_dir / f"{year}.json"
        if not args.allow_shrink:
            check_not_shrinking(year, {h["date"] for h in holiday_list}, path)

        written = write_document(build_document(year, holiday_list, today), args.output_dir)
        print(f"{written.name}: {len(holiday_list)}건 ({source_label()}, updated {today})")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
