"""가이드 폴더(eye/, face/)의 routine.json을 읽어 운동별 음성 가이드 MP3를 만든다.

안내 문장 + 무음 = 정해진 초가 되게 이어 붙여 속도를 강제하고,
반복 운동은 "하나, 둘…"을 문장 앞에 붙여 횟수를 소리 내어 센다.
폴더마다 만드는 것:
  audio/NN.mp3  웹 앱용 48k MP3
  guides.json   운동별 길이와 part마다 각 회가 끝나는 시각 (앱이 횟수를 세고 건너뛸 때 씀)
  대본.md       타임라인을 그대로 풀어 쓴 대본
  mp3/          128k MP3 (휴대폰에 넣어 듣기용, 커밋하지 않음)
사용: python build_guide.py [eye] [face]   (없으면 둘 다)
"""
import asyncio
import hashlib
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import edge_tts

ROOT = Path(__file__).parent
CACHE = ROOT / "tts_cache"
GUIDES = ("eye", "face")
RATE = 24000  # edge-tts 출력과 같은 샘플레이트, 모노 16비트
UNITS = ["", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉"]
COUNTS = [t + u for t in ("", "열", "스물", "서른", "마흔") for u in UNITS][1:] + ["쉰"]  # 하나 … 쉰
# TTS 클립 앞뒤의 여백을 잘라서 routine.json의 seconds가 그대로 지켜지게 한다
TRIM = ("silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-50dB,areverse")
BEEP_GAP = 0.4  # 비프 다음 무음
# steps의 seconds는 안내가 시작될 때부터 잰다 ("감고" 3초 = 음성 포함 3초).
# 다만 긴 문장은 다 듣고 나서야 움직이므로, 음성은 1.5초와 동작 시간의 절반까지만 시간에 넣는다
MAX_VOICE_IN = 1.5


def build_timeline(r, ex):
    """운동 하나의 ("say", 문장) / ("silence", 초) / ("beep",) 목록.

    parts가 있으면 운동 안내 다음에 각 부분(좌우·상하 등)을 차례로 이어 붙인다.
    """
    t = [("say", ex["name"].split(" - ")[0] + " 운동."), ("silence", 1)] if r.get("announce_name") else []  # "쿼카 운동."
    t.append(("beep",))
    for part in [ex] + ex.get("parts", []):
        t.append(("part",))  # 회차 시각을 part별로 묶는다 (앱이 part마다 횟수를 줄일 때 씀)
        if "intro" in part:
            t += [("say", part["intro"]), ("silence", r["pause_after_intro"])]
        brk = part.get("break")  # every회마다 한 번 쉬기
        for rep in range(part.get("repetitions", 0)):
            for i, step in enumerate(part["steps"]):
                if step.get("tone"):  # 말 대신 짧은 "똑" 소리 (빠른 동작용)
                    t += [("tick",), ("silence", step["seconds"])]
                    continue
                text = step["cue"] if rep == 0 else step.get("short_cue", step["cue"])
                if part["count_aloud"] and i == 0:
                    text = f"{COUNTS[rep]}, {text}" if text else COUNTS[rep]
                mid = step.get("mid_cue")  # 무음 중간에 every초마다 짧은 안내 (예: 깜빡)
                if mid:
                    n = round(step["seconds"] / mid["every"])
                    t += [("say_fit", text, mid["every"])] + [("say_fit", mid["cue"], mid["every"])] * (n - 1)
                else:
                    t.append(("say_fit", text, step["seconds"]))
            t.append(("rep",))  # 한 회가 끝난 지점 (소리 없음, 앱이 횟수를 셀 때 씀)
            if brk and (rep + 1) % brk["every"] == 0 and rep + 1 < part["repetitions"]:
                t += [("say", brk["cue"]), ("silence", brk["seconds"])]
        if "outro" in part:
            t += [("say", part["outro"]["cue"]), ("silence", part["outro"]["seconds"])]
    t += [("beep",), ("say", r["closing"]), ("silence", 1)]
    return t


async def synth(texts, voice, rate):
    """문장마다 TTS → 앞뒤 무음 자른 WAV. 같은 목소리·속도·문장은 캐시 재사용."""
    CACHE.mkdir(exist_ok=True)
    wavs = {}
    for n, text in enumerate(texts, 1):
        key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
        mp3, wav = CACHE / f"{key}.mp3", CACHE / f"{key}.wav"
        if not wav.exists():
            print(f"  TTS {n}/{len(texts)}: {text[:30]}")
            await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-af", TRIM,
                            "-ac", "1", "-ar", str(RATE), "-sample_fmt", "s16", str(wav)], check=True)
        wavs[text] = wav
    return wavs


def silence(sec):
    return b"\x00\x00" * round(sec * RATE)


def beep(sec=0.3, freq=880):
    n = round(sec * RATE)
    fade = RATE // 100  # 10ms 페이드로 '딱' 소리 방지
    return b"".join(
        struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / RATE)
                              * min(1, i / fade, (n - i) / fade)))
        for i in range(n))


def render(timeline, wavs, out_mp3, web_mp3):
    """타임라인을 이어 붙여 MP3로 저장. (길이 초, 연속 무음 길이 목록, part별 회차가 끝난 시각 목록) 반환."""
    pcm, runs, run, reps = bytearray(), [], 0.0, []
    for item in timeline:
        kind = item[0]
        if kind == "part":
            reps.append([])
        elif kind == "rep":
            reps[-1].append(round(len(pcm) / 2 / RATE, 2))
        elif kind == "silence":
            pcm += silence(item[1])
            run += item[1]
        elif kind == "beep":
            runs.append(run)
            pcm += beep() + silence(BEEP_GAP)
            run = BEEP_GAP
        elif kind == "tick":
            runs.append(run)
            pcm += beep(0.06, 1320)
            run = 0.0
        else:
            runs.append(run)
            with wave.open(str(wavs[item[1]])) as w:
                clip = w.readframes(w.getnframes())
            pcm += clip
            run = 0.0
            if kind == "say_fit":  # 음성 + 남은 무음 = item[2]초 (긴 음성은 MAX_VOICE_IN 참고)
                rest = item[2] - min(len(clip) / 2 / RATE, MAX_VOICE_IN, item[2] / 2)
                pcm += silence(rest)
                run = rest
    runs.append(run)

    tmp_wav = out_mp3.with_suffix(".wav")
    with wave.open(str(tmp_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_wav),
                    "-b:a", "128k", str(out_mp3)], check=True)
    # 웹용은 말소리라 48k로 충분하다 (20분짜리도 7MB 안쪽)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_wav),
                    "-b:a", "48k", str(web_mp3)], check=True)
    tmp_wav.unlink()
    return len(pcm) / 2 / RATE, runs, [p for p in reps if p]


def write_script(r, timelines, path):
    """타임라인을 사람이 읽는 대본으로 풀어 쓴다."""
    lines = [f"# {r['title']} 가이드 대본", ""]
    for n, (ex, t) in enumerate(zip(r["exercises"], timelines), 1):
        lines += ["", f"## {n:02d} {ex['name']}", ""]
        for item in t:
            kind = item[0]
            if kind == "beep":
                lines.append("[삐]")
            elif kind == "tick":
                lines.append("[똑]")
            elif kind == "say":
                lines.append(item[1])
            elif kind == "silence":
                lines.append(f"  (무음 {item[1]:g}초)")
            elif kind == "say_fit":
                lines.append(f"{item[1]}  (음성 포함 {item[2]:g}초)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(name):
    d = ROOT / name
    r = json.loads((d / "routine.json").read_text(encoding="utf-8"))
    timelines = [build_timeline(r, ex) for ex in r["exercises"]]
    texts = list(dict.fromkeys(x[1] for t in timelines for x in t if x[0] in ("say", "say_fit")))
    print(f"[{name}] 문장 {len(texts)}개 합성")
    wavs = asyncio.run(synth(texts, r["voice"], r["rate"]))

    (d / "mp3").mkdir(exist_ok=True)
    (d / "audio").mkdir(exist_ok=True)
    guides = []
    for n, (ex, t) in enumerate(zip(r["exercises"], timelines), 1):
        out = d / "mp3" / f"{n:02d} {ex['name']}.mp3"
        web = f"audio/{n:02d}.mp3"
        total, runs, reps = render(t, wavs, out, d / web)
        guides.append({"n": n, "id": ex["id"], "name": ex["name"], "intro": ex["intro"],
                       "file": web, "duration": round(total, 2), "repEnds": reps})
        quiet = ", ".join(f"{s}초+ {sum(x >= s for x in runs)}개" for s in (2.8, 9.5))
        print(f"{out.name}: {int(total // 60)}분 {int(total % 60):02d}초 ({total:.1f}초) | 무음 {quiet}")
    (d / "guides.json").write_text(json.dumps(guides, ensure_ascii=False, indent=1), encoding="utf-8")
    write_script(r, timelines, d / "대본.md")


def main():
    for name in sys.argv[1:] or GUIDES:
        build(name)


if __name__ == "__main__":
    main()
