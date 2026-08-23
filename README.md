# shiftmate-holidays

교대근무 알림 앱 **shiftmate**가 받아가는 **한국 공휴일 데이터**를 두는 곳이다.

앱은 이 저장소의 raw JSON만 읽는다. **공공데이터포털 API를 앱에서 직접 부르지 않는다** —
API 키가 APK 디컴파일로 노출되고, 일 10,000건 한도를 전체 사용자가 공유하므로 사용자가
늘면 즉시 초과하기 때문이다.

```
https://raw.githubusercontent.com/kimash0420/shiftmate-holidays/main/holidays/2026.json
```

## 데이터 형식

```json
{
  "year": 2026,
  "updated": "2026-08-23",
  "holidays": [
    { "date": "2026-01-01", "name": "1월 1일" },
    { "date": "2026-03-01", "name": "삼일절" }
  ]
}
```

- **법정공휴일·대체공휴일·임시공휴일**을 모두 담는다(API 응답의 `isHoliday == "Y"`).
  식목일처럼 **쉬지 않는 기념일은 넣지 않는다** — 넣으면 근무일이 통째로 휴무가 된다.
- `holidays`는 날짜순으로 정렬돼 있고 날짜가 중복되지 않는다.
- **`updated`는 마지막으로 성공적으로 조회한 날짜다.** 공휴일 목록이 그대로여도 갱신된다.
  앱은 이 값으로 "아직 관리되고 있는가"를 판단해 30일 넘게 멈추면 경고 배너를 띄운다.
  조회에 실패하면 워크플로가 **아무것도 커밋하지 않으므로** 날짜가 자연히 멈춘다.

## 자동 갱신

`.github/workflows/update-holidays.yml`이 **매주 월요일 05:20 KST**에 올해·내년치를 다시
받아 커밋한다. 임시공휴일이 지정되면 저장소 **Actions 탭 → update-holidays → Run workflow**로
즉시 돌린다.

> 주 1회인 이유: 가장 큰 리스크가 "임시공휴일이 지정됐는데 갱신이 늦어 전체 사용자의
> 알림이 틀리는 것"이라 반영 지연을 최대 30일에서 7일로 줄였다. 공개 저장소라 Actions
> 사용량 비용이 0이다.

## 설정 (최초 1회)

1. [공공데이터포털](https://www.data.go.kr)에서 **「한국천문연구원 특일 정보」** 활용신청.
2. 마이페이지에서 **일반 인증키**를 확인한다. 두 벌로 보여주는데 **`Decoding` 쪽**을 쓴다.
   > `Encoding` 쪽을 넣으면 요청 시 이중 인코딩되어 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가
   > 난다. 증상이 "등록되지 않은 키"라서 원인이 키 **형태**라는 것이 보이지 않는다.
   > (스크립트가 자동으로 되돌리기는 하지만 경고를 남긴다.)
3. 이 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - 이름: `SERVICE_KEY`
   - 값: 위 Decoding 키

## 직접 실행

```bash
pip install -r requirements.txt
SERVICE_KEY="<Decoding 키>" python scripts/fetch_holidays.py          # 올해 + 내년
SERVICE_KEY="<Decoding 키>" python scripts/fetch_holidays.py 2027     # 연도 지정
```

테스트(네트워크 없이 돈다):

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## 임시공휴일을 손으로 넣어야 할 때

API 반영이 늦으면 `holidays/<연도>.json`을 직접 고쳐 커밋해도 된다. 날짜순 정렬과
`updated` 갱신만 지키면 앱은 구분하지 않는다. 다음 자동 실행이 API 응답으로 덮어쓰므로
**API에 아직 없는 날짜는 그때 사라진다** — 앱의 «수동 휴일 지정»으로도 대응할 수 있다.

## 이 저장소의 출처

개발 원본은 `AUTOPILOT/shiftmate-holidays/`이고 여기로 `git subtree split`해 게시한다.
**이 저장소에서 직접 고치면 다음 게시에 덮인다** — 워크플로가 만드는 `holidays/` 커밋은 예외다.
