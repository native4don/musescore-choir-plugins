"""A WAV is valid when it is whole and has audio in it -- whichever kind it is.

MuseScore 4 writes its audio as 32-bit floating point (WAV format 3). Measured on
MuseScore Studio 4.7.5, October 2026:

    RIFF (little-endian) data, WAVE audio, IEEE Float, stereo 44100 Hz

`audio._valid_wav` asked Python's `wave` module to open the file, and that module
reads integer PCM (format 1) and nothing else: `wave.Error: unknown format: 3`. So
every mix MuseScore 4 rendered was called "an invalid WAV audio mix" and Record
stopped there, with a complete, playable file on disk.

The same module had the opposite fault as well. It believes the header, so a file
cut short -- a header promising more audio than the file holds -- opened without
complaint and was accepted.

What a valid mix is, then: a RIFF/WAVE file that names at least one channel and a
sample rate, and whose audio data is there, all of it.
"""

import os
import stat
import struct
import sys
import wave

import pytest
from lxml import etree

from src.scrollvideo import audio
from src.scrollvideo.audio import render_mix_cached
from src.scrollvideo.tests.conftest import needs_musescore

FLOAT, PCM = 3, 1

SCORE = """<museScore><Score>
  <Part><trackName>S1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
  <Part><trackName>B1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
</Score></museScore>"""


def _wav_bytes(fmt=FLOAT, channels=2, rate=44100, bits=32, frames=100,
               declared_frames=None) -> bytes:
    """A WAV built by hand, so its kind and its honesty are the test's to choose.

    `declared_frames` is what the data chunk *says* it holds; `frames` is what it
    does hold. A float WAV carries the 18-byte `fmt ` chunk MuseScore 4 writes.
    """
    block = channels * bits // 8
    data = b"\x00" * (block * frames)
    declared = block * (frames if declared_frames is None else declared_frames)
    fmt_body = struct.pack("<HHIIHH", fmt, channels, rate, rate * block, block, bits)
    if fmt == FLOAT:
        fmt_body += struct.pack("<H", 0)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
            + b"data" + struct.pack("<I", declared) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _file(tmp_path, data: bytes, name="mix.wav") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


# --- what counts as a valid mix --------------------------------------------


def test_the_floating_point_wav_musescore_4_writes_is_valid(tmp_path):
    assert audio._valid_wav(_file(tmp_path, _wav_bytes(fmt=FLOAT))) is True


def test_an_integer_wav_is_still_valid(tmp_path):
    path = tmp_path / "mix.wav"
    with wave.open(os.fspath(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * 10)
    assert audio._valid_wav(str(path)) is True


def test_no_file_is_not_a_valid_mix(tmp_path):
    assert audio._valid_wav(str(tmp_path / "never.wav")) is False


def test_an_empty_file_is_not_a_valid_mix(tmp_path):
    assert audio._valid_wav(_file(tmp_path, b"")) is False


def test_a_file_that_is_not_a_wav_is_not_a_valid_mix(tmp_path):
    assert audio._valid_wav(_file(tmp_path, b"not a wav")) is False


def test_a_wav_cut_short_is_not_a_valid_mix(tmp_path):
    """The header promises twice the audio the file holds."""
    cut = _wav_bytes(fmt=PCM, channels=1, rate=8000, bits=16,
                     frames=100, declared_frames=200)
    assert audio._valid_wav(_file(tmp_path, cut)) is False


def test_a_wav_with_no_audio_in_it_is_not_a_valid_mix(tmp_path):
    silent = _wav_bytes(fmt=FLOAT, frames=0)
    assert audio._valid_wav(_file(tmp_path, silent)) is False


def test_a_wav_that_names_no_channels_is_not_a_valid_mix(tmp_path):
    fmt_body = struct.pack("<HHIIHH", PCM, 0, 8000, 0, 0, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_body)) + fmt_body
            + b"data" + struct.pack("<I", 8) + b"\x00" * 8)
    data = b"RIFF" + struct.pack("<I", len(body)) + body
    assert audio._valid_wav(_file(tmp_path, data)) is False


# --- the audio step, end to end ---------------------------------------------


FAKE_SOURCE = '''#!{python}
import os
import shutil
import sys

argv = sys.argv[1:]
shutil.copyfile(os.environ["FAKE_MUSESCORE_WAV"], argv[argv.index("-o") + 1])
'''


def test_a_floating_point_mix_gets_through_the_audio_step_and_is_reused(
        tmp_path, monkeypatch):
    """A stand-in MuseScore that writes what MuseScore 4 writes."""
    sample = _file(tmp_path, _wav_bytes(fmt=FLOAT), "sample.wav")
    script = tmp_path / "fake_mscore"
    script.write_text(FAKE_SOURCE.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_MUSESCORE_WAV", sample)
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(script))
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    cache = str(tmp_path / "cache")

    first, first_reused = render_mix_cached(str(score), "S1", cache)
    second, second_reused = render_mix_cached(str(score), "S1", cache)

    assert first == second
    assert (first_reused, second_reused) == (False, True)
    assert audio._valid_wav(first) is True


@needs_musescore
def test_a_mix_rendered_by_the_installed_musescore_is_valid(fermata_mscx, tmp_path):
    """The test that would have caught it: real audio from the real program.

    Whatever kind of WAV the installed MuseScore writes -- integer from MuseScore 3,
    floating point from MuseScore 4 -- the audio step has to take it.
    """
    names = audio.part_names(etree.parse(fermata_mscx).getroot())
    mix, reused = render_mix_cached(fermata_mscx, names[0], str(tmp_path / "cache"))

    assert reused is False
    assert os.path.getsize(mix) > 0
    assert audio._valid_wav(mix) is True
