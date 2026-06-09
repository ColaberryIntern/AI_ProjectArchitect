"""Quick A/B test of edge-tts voices to pick the best one for training narration."""
import asyncio
import edge_tts
from pathlib import Path

OUT = Path(__file__).parent / "_voice_samples"
OUT.mkdir(exist_ok=True)

SAMPLE = (
    "This is My Day. It's the page you open in the morning, and it's the page "
    "that should answer the question: what do I work on right now?"
)

# Top neural voices for English narration
VOICES = [
    "en-US-AvaNeural",      # warm, professional female
    "en-US-AvaMultilingualNeural",
    "en-US-EmmaNeural",     # younger, energetic
    "en-US-AndrewNeural",   # warm, professional male
    "en-US-BrianNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
]


async def gen(voice: str):
    out_path = OUT / f"voice-{voice}.mp3"
    communicate = edge_tts.Communicate(SAMPLE, voice, rate="-5%")
    await communicate.save(str(out_path))
    print(f"OK -> {out_path.name}")


async def main():
    for v in VOICES:
        try:
            await gen(v)
        except Exception as e:
            print(f"FAIL {v}: {e}")


asyncio.run(main())
