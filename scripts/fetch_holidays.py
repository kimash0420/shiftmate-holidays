#!/usr/bin/env python3
"""한국천문연구원 특일 정보 API로 연도별 공휴일 JSON을 만든다.

shiftmate 앱은 이 저장소의 raw JSON만 받아간다(명세 4.1). **서비스 키는 앱에 들어가지
않는다** — APK를 디컴파일하면 노출되고, 일 10,000건 한도를 전체 사용자가 공유하므로
사용자가 늘면 즉시 초과한다.

사용:
    SERVICE_KEY=... python scripts/fetch_holidays.py            # 올해 + 내년
    SERVICE_KEY=... python scripts/fetch_holidays.py 2026 2027  # 연도 지정
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests

API_URL = (
    "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
)

#: 네트워크 호출에는 반드시 타임아웃을 건다. (연결, 읽기)
TIMEOUT = (5, 20)

#: 일시적 실패만 재시도한다. 키가 틀린 것은 재시도해도 낫지 않는다.
RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

KST = timezone(timedelta(hours=9))

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "holidays"


class FetchError(RuntimeError):
    """API 응답을 신뢰할 수 없다. **부분 데이터로 파일을 덮어쓰지 않기 위해** 던진다."""


def normalize_service_key(raw: str) -> str:
    """data.go.kr이 주는 두 형태 중 어느 것을 붙여넣어도 동작하게 한다.

    포털은 같은 키를 `Encoding`(퍼센트 인코딩)과 `Decoding`(원본) 두 벌로 보여준다.
    `requests`의 ``params=``는 값을 **다시** 인코딩하므로 Encoding 쪽을 그대로 넣으면
    `%2B`가 `%252B`가 되어 ``SERVICE_KEY_IS_NOT_REGISTERED_ERROR``가 난다.
    증상이 "키가 등록되지 않았다"라서 원인이 키 형태라는 것이 보이지 않는다.
    """
    if "%" in raw:
        decoded = urllib.parse.unquote(raw)
        if decoded != raw:
            print(
                "[warn] 서비스 키가 퍼센트 인코딩돼 있어 디코딩했다 "
                "(포털의 'Decoding' 키를 쓰는 편이 낫다).",
                file=sys.stderr,
            )
            return decoded
    return raw


def parse_items(payload: Any) -> list[dict[str, Any]]:
    """응답 본문에서 항목 목록만 꺼낸다.

    이 API는 모양이 셋으로 흔들린다 — 항목이 여럿이면 리스트, **하나면 딕셔너리**,
    없으면 빈 문자열이다. 시스템 경계에서 한 번에 흡수한다.
    """
    if not isinstance(payload, dict):
        raise FetchError(f"응답이 객체가 아니다: {type(payload).__name__}")

    response = payload.get("response")
    if not isinstance(response, dict):
        raise FetchError("응답에 response가 없다")

    header = response.get("header") or {}
    code = str(header.get("resultCode", "")).strip()
    if code == "03":  # NODATA — 그 달에 공휴일이 없다. 정상이다.
        return []
    if code not in ("00", "0"):
        raise FetchError(
            f"API가 실패를 반환했다: resultCode={code!r} "
            f"resultMsg={header.get('resultMsg')!r}"
        )

    body = response.get("body") or {}
    items = body.get("items")
    if not items:  # "" 또는 None — 그 달에 항목이 없다.
        return []
    if isinstance(items, str):
        return []

    item = items.get("item") if isinstance(items, dict) else None
    if item is None:
        return []
    if isinstance(item, dict):
        return [item]
    if isinstance(item, list):
        return [i for i in item if isinstance(i, dict)]
    raise FetchError(f"item의 모양을 모르겠다: {type(item).__name__}")


def to_holidays(items: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """`isHoliday == "Y"`인 항목만 남겨 `{date, name}`으로 바꾼다(명세 4.3).

    이 API는 공휴일이 아닌 기념일(예: 식목일)도 함께 준다. 그것들을 그대로 넣으면
    **근무일이 통째로 휴무로 바뀐다.**
    """
    result: dict[str, str] = {}
    for item in items:
        if str(item.get("isHoliday", "")).strip().upper() != "Y":
            continue
        locdate = str(item.get("locdate", "")).strip()
        if len(locdate) != 8 or not locdate.isdigit():
            raise FetchError(f"locdate 형식이 이상하다: {locdate!r}")
        name = str(item.get("dateName", "")).strip()
        if not name:
            raise FetchError(f"dateName이 비었다: locdate={locdate}")
        iso = f"{locdate[:4]}-{locdate[4:6]}-{locdate[6:]}"
        # 같은 날짜가 두 이름으로 오는 경우가 있다(예: 설날 연휴와 대체공휴일).
        # 먼저 온 이름을 남긴다 — 날짜가 휴일인지가 앱이 쓰는 전부다.
        result.setdefault(iso, name)
    return [{"date": d, "name": result[d]} for d in sorted(result)]


def build_document(year: int, holidays: list[dict[str, str]], updated: date) -> dict:
    """명세 4.2의 스키마.

    `updated`는 **마지막으로 성공적으로 조회한 날**이다. 내용이 그대로여도 갱신한다 —
    앱은 이 값으로 "개발자가 아직 관리하고 있는가"를 판단하고 30일 넘게 멈추면 경고
    배너를 띄운다(명세 4.5). 내용이 안 바뀌었다고 날짜를 안 올리면 **정상 동작이
    방치로 보인다.** 반대로 조회에 실패하면 이 스크립트는 아무것도 쓰지 않고 죽는다
    (그래야 날짜가 멈추고 경고가 뜬다).
    """
    return {
        "year": year,
        "updated": updated.isoformat(),
        "holidays": holidays,
    }


def fetch_month(session: requests.Session, key: str, year: int, month: int) -> list[dict]:
    params = {
        "serviceKey": key,
        "solYear": str(year),
        "solMonth": f"{month:02d}",
        "numOfRows": "100",
        "_type": "json",
    }
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            response = session.get(API_URL, params=params, timeout=TIMEOUT)
            if response.status_code != 200:
                raise FetchError(
                    f"HTTP {response.status_code}: {response.text[:200]!r}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                # 키가 틀리면 `_type=json`이어도 XML 오류 문서가 온다. 본문을 남겨야
                # 원인이 보인다 — 이 지점의 침묵이 디버깅을 가장 오래 끌었다.
                raise FetchError(
                    f"JSON이 아니다(키 문제일 가능성이 높다): {response.text[:200]!r}"
                ) from exc
            return parse_items(payload)
        except (requests.RequestException, FetchError) as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise FetchError(f"{year}-{month:02d} 조회 실패: {last_error}")


def fetch_year(session: requests.Session, key: str, year: int) -> list[dict[str, str]]:
    items: list[dict] = []
    for month in range(1, 13):
        items.extend(fetch_month(session, key, year, month))
    return to_holidays(items)


def write_document(document: dict, output_dir: Path = OUTPUT_DIR) -> Path:
    """원자적으로 쓴다 — 중간에 죽어도 반쯤 쓰인 JSON이 남으면 앱이 그것을 받아간다."""
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
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="JSON을 쓸 디렉토리 (기본: holidays/)",
    )
    args = parser.parse_args(argv)

    raw_key = os.environ.get("SERVICE_KEY", "").strip()
    if not raw_key:
        print("SERVICE_KEY 환경변수가 없다.", file=sys.stderr)
        return 2
    key = normalize_service_key(raw_key)

    today = datetime.now(KST).date()
    years = args.years or default_years(today)

    with requests.Session() as session:
        for year in years:
            holidays = fetch_year(session, key, year)
            if not holidays:
                # 한 해에 공휴일이 0건일 수는 없다. 조용히 빈 파일을 쓰면 그날부터
                # 모든 사용자의 휴무 판정이 사라진다.
                raise FetchError(f"{year}년 공휴일이 0건이다 — 응답을 신뢰할 수 없다")
            path = write_document(build_document(year, holidays, today), args.output_dir)
            print(f"{path.name}: {len(holidays)}건 (updated {today.isoformat()})")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FetchError as exc:
        # 실패하면 아무것도 쓰지 않고 죽는다. 워크플로가 커밋하지 않으므로
        # `updated`가 멈추고, 앱이 30일 뒤 경고 배너를 띄운다(명세 4.5).
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
