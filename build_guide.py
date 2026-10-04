"""routine.json을 읽어 운동별 얼굴 운동 음성 가이드 MP3를 만든다.

안내 문장 → (정해진 초만큼 무음) 을 이어 붙여 속도를 강제하고,
반복 운동은 "하나, 둘…"을 문장 앞에 붙여 횟수를 소리 내어 센다.
"""
import asyncio
import hashlib
import json
import math
import struct
import subprocess
import wave
from pathlib import Path

import edge_tts

ROOT = Path(__file__).parent
CACHE = ROOT / "tts_cache"
OUT_DIR = ROOT / "guides"
RATE = 24000  # edge-tts 출력과 같은 샘플레이트, 모노 16비트
UNITS = ["", "하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉"]
COUNTS = [t + u for t in ("", "열", "스물", "서른", "마흔") for u in UNITS][1:] + ["쉰"]  # 하나 … 쉰
# TTS 클립 앞뒤의 여백을 잘라서 routine.json의 seconds가 그대로 지켜지게 한다
TRIM = ("silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-50dB,areverse")
BEEP_GAP = 0.4  # 비프 다음 무음


def build_timeline(r, ex):
    """운동 하나의 ("say", 문장) / ("silence", 초) / ("beep",) 목록.

    parts가 있으면 운동 안내 다음에 각 부분(좌우·상하 등)을 차례로 이어 붙인다.
    """
    t = [("say", ex["name"].split(" - ")[0] + " 운동."), ("silence", 1), ("beep",)]  # "쿼카 운동."
    for part in [ex] + ex.get("parts", []):
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
                    num = COUNTS[part.get("count_start", 0) + rep]  # 앞 part에 이어서 세기
                    text = f"{num}, {text}" if text else num
                if step.get("fit"):  # 음성 + 무음 = 정확히 seconds초 (빠른 반복용)
                    t.append(("say_fit", text, step["seconds"]))
                    continue
                t.append(("say", text))
                mid = step.get("mid_cue")  # 무음 중간에 every초마다 짧은 안내 (예: 깜빡)
                if mid:
                    n = round(step["seconds"] / mid["every"])
                    # 안내 음성 길이만큼 무음을 줄여 구간 길이(seconds)를 그대로 지킨다
                    t += [("silence", mid["every"])] + [("say_fit", mid["cue"], mid["every"])] * (n - 1)
                else:
                    t.append(("silence", step["seconds"]))
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


def render(timeline, wavs, out_mp3):
    """타임라인을 이어 붙여 MP3로 저장. (길이 초, 연속 무음 길이 목록) 반환."""
    pcm, runs, run = bytearray(), [], 0.0
    for item in timeline:
        kind = item[0]
        if kind == "silence":
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
            if kind == "say_fit":  # 음성 + 남은 무음 = 정확히 item[2]초
                rest = max(0.0, item[2] - len(clip) / 2 / RATE)
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
    tmp_wav.unlink()
    return len(pcm) / 2 / RATE, runs


def main():
    r = json.loads((ROOT / "routine.json").read_text(encoding="utf-8"))
    timelines = [build_timeline(r, ex) for ex in r["exercises"]]
    texts = list(dict.fromkeys(x[1] for t in timelines for x in t if x[0] in ("say", "say_fit")))
    print(f"문장 {len(texts)}개 합성")
    wavs = asyncio.run(synth(texts, r["voice"], r["rate"]))

    OUT_DIR.mkdir(exist_ok=True)
    for n, (ex, t) in enumerate(zip(r["exercises"], timelines), 1):
        out = OUT_DIR / f"{n:02d} {ex['name']}.mp3"
        total, runs = render(t, wavs, out)
        quiet = ", ".join(f"{d}초+ {sum(x >= d for x in runs)}개" for d in (2.8, 4.8))
        print(f"{out.name}: {int(total // 60)}분 {int(total % 60):02d}초 ({total:.1f}초) | 무음 {quiet}")


if __name__ == "__main__":
    main()
