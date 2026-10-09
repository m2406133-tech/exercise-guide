# 눈·얼굴 운동 가이드

혼자 하면 너무 빨리 하게 되는 눈운동(14가지)과 얼굴운동(7가지)을 정해진 속도로 안내하고 횟수를 소리 내어 세 주는 웹 앱.
Google로 로그인하면 기록이 계정에 저장돼 휴대폰이든 PC든 같은 기록이 보인다. 로그인하지 않아도 운동은 할 수 있다(기록만 안 남음).

주소: https://m2406133-tech.github.io/face-exercise-guide/ (GitHub Pages, `main` 브랜치 루트)

## 파일
- `index.html` — 앱 전부 (눈운동/얼굴운동/기록 탭, 재생 바, 횟수 고르기, 로그인, 기록)
- `firestore.rules` — 기록 보안 규칙. 로그인한 사람은 `users/<자기 uid>` 아래만 읽고 쓴다. Firebase 콘솔 → Firestore → 규칙에 붙여넣는다
- `build_guide.py` — `eye/`·`face/`의 `routine.json`을 읽어 음성 파일을 만든다
- `eye/`, `face/` — 가이드마다
  - `routine.json` 운동 내용 전부 (안내 문장, 동작 시간, 반복 횟수). 고칠 때는 이 파일만 고친다
  - `audio/NN.mp3` 앱이 쓰는 48k MP3, `guides.json` 운동별 길이와 part마다 각 회가 끝나는 시각
  - `대본.md` 실제로 나오는 음성을 순서대로 풀어 쓴 대본, `README.md` 운동을 고른 근거
  - `mp3/` 128k MP3 (휴대폰에 넣어 듣기용, 커밋하지 않음)
- `tts_cache/` — 합성한 문장 캐시 (커밋하지 않음, 지워도 다시 만들어짐)

## 다시 만들기
```bash
pip install -r requirements.txt
python build_guide.py
```
- `python build_guide.py face`처럼 한 가이드만 만들 수도 있다
- ffmpeg가 PATH에 있어야 하고, 음성 합성(edge-tts)은 인터넷 연결이 필요하다
- 바뀐 `audio/`·`guides.json`을 커밋해 `main`에 올리면 앱에 반영된다

## routine.json 고치는 법
- `steps[].seconds` — 안내가 시작될 때부터 다음 안내까지의 시간 ("감고" 3초 = 음성 포함 3초).
  긴 문장은 다 들어야 움직일 수 있으므로 음성은 1.5초, 그리고 seconds의 절반까지만 시간에 포함한다 (`MAX_VOICE_IN`)
- `steps[].cue` — 1회차 안내, `short_cue` — 2회차부터 쓰는 짧은 안내 (`""`이면 숫자만)
- `repetitions` — 반복 횟수(앱에서 고를 수 있는 최대), `count_aloud` — 각 회 첫 안내 앞에 "하나, 둘…"을 붙일지 (쉰까지)
- `outro` — 반복이 다 끝난 뒤 한 번 나오는 안내
- `parts` — 한 파일 안에서 좌우·상하처럼 단계를 나눌 때. 앱에서는 "방향마다 N회"로 고른다
- `break` — `{every, cue, seconds}`: every회마다 한 번 나오는 안내 (얼굴 쿼카 25회째, 눈 14번 5회마다)
- `steps[].tone: true` — 말 대신 짧은 "똑" 소리 (눈 05·13번), `steps[].mid_cue` — 동작 중간에 every초마다 짧은 안내 (예: 깜빡)
- `announce_name` — 시작할 때 "오리 운동."처럼 이름을 말한다 (얼굴), `title` — 대본 제목
- 안내 문장에는 "열 번 합니다" 같은 총 횟수를 넣지 않는다. 앱에서 횟수를 줄이면 틀린 말이 되기 때문
- `voice` / `rate` — 목소리(`ko-KR-SunHiNeural` 등)와 속도(`-5%` 등)

## 앱이 횟수를 줄이는 방식
MP3는 항상 최대 횟수로 만들어 두고, 고른 횟수를 채우면 그 part의 남은 회차를 건너뛰어 마무리 안내로 간다(회차 끝 0.15초 전, 무음 구간에서).
고른 횟수와 "다음 운동 이어서"는 그 기기 브라우저에만 저장된다. 화면이 꺼지면 브라우저가 페이지를 멈춰 건너뛰기를 못 할 수 있어, 재생 중에는 화면이 꺼지지 않게 해 두었다.

## 기록 (Firebase)
- 로그인: Firebase Authentication의 Google 로그인 (팝업). 카카오톡·라인 앱 안에서 열면 바깥 브라우저로 넘긴다(Google이 앱 안 브라우저 로그인을 막음)
- 저장: Firestore `users/<uid>/days/<YYYY-MM-DD>` = `{date, sessions: {sid: {guide, ex, name, n, startedAt, durationSec, reps, repsTotal, completed}}}`. 15초 넘게 한 운동만, 30초마다 중간 저장
- 프로젝트 주인은 Firebase 콘솔에서 모든 사람의 기록을 볼 수 있다 (로그인 화면에 안내함). 지워 달라는 요청이 오면 콘솔 → Firestore에서 `users/<uid>`를 지운다
- 무료(Spark) 요금제로 두고 결제 수단을 연결하지 않는다. 남용돼도 한도에서 멈출 뿐 청구되지 않는다
- 예전 얼굴운동 페이지가 기기 브라우저에 남긴 기록(`face-ex-log-v1`)은 그 기기에서 처음 로그인할 때 가져올지 묻는다
- `index.html`의 `FIREBASE_CONFIG`가 `null`이면 기록을 탭 메모리에만 두는 화면 확인용 모드로 돈다
