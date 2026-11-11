"""Processo isolado. Recebe áudio pelo stdin e devolve somente a transcrição."""

import subprocess
import sys


def main() -> int:
    import numpy as np
    from faster_whisper import WhisperModel

    model_dir, seconds = sys.argv[1], int(sys.argv[2])
    data = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if not data or len(data) > 8 * 1024 * 1024:
        return 2
    converted = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "pipe",
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-t",
            str(seconds + 1),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        input=data,
        capture_output=True,
        timeout=30,
        check=False,
    )
    samples = np.frombuffer(converted.stdout, dtype="<f4")
    if converted.returncode or not 1600 <= len(samples) <= seconds * 16000:
        return 2
    if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 0.0001:
        return 2
    model = WhisperModel(
        model_dir,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
        local_files_only=True,
    )
    segments, _ = model.transcribe(
        samples,
        language="pt",
        beam_size=3,
        temperature=0,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    words = []
    for segment in segments:
        if (
            segment.avg_logprob < -1.0
            or segment.no_speech_prob > 0.75
            or segment.compression_ratio > 2.4
        ):
            return 2
        words.append(segment.text.strip())
    text = " ".join(words).strip()
    if not text or len(text) > 4000:
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
