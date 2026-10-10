"""The voice a practice track is named for has to be louder -- measured, not assumed.

MuseScore 4 ignores the controller 7 volume `set_mix` writes into the score. Every
mix it rendered held all parts at one level: nine files, nine names, one sound
(heard on "In the Bleak Midwinter", October 2026). It takes each part's loudness
from `audiosettings.json` beside the score, matched on the part's number and its
instrument name.

These tests ask the installed MuseScore for real audio and measure it. The score
has two parts that never sing together: the first sings bar 1 only, the second bar
2 only, the same notes. So the level of bar 1 against bar 2 in a mix is the level
of one part against the other, and nothing else.

The second test is the case that mattered on the day: a cleaned score in which
every part carries the same number and the same instrument name, so MuseScore 4
has one track for all of them.
"""

import array
import os
import struct

import pytest
from lxml import etree

from src.scrollvideo import audio
from src.scrollvideo.tests.conftest import needs_musescore

BAR = """<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>""" * 4
REST = """<note><rest measure="yes"/><duration>4</duration></note>"""
OPENING = """<attributes><divisions>1</divisions><key><fifths>0</fifths></key>
  <time><beats>4</beats><beat-type>4</beat-type></time>
  <clef><sign>G</sign><line>2</line></clef></attributes>
  <direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>
  <per-minute>120</per-minute></metronome></direction-type><sound tempo="120"/></direction>"""

MUSICXML = f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1"><part-name>S1</part-name></score-part>
    <score-part id="P2"><part-name>B1</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">{OPENING}{BAR}</measure>
    <measure number="2">{REST}</measure>
  </part>
  <part id="P2">
    <measure number="1">{OPENING}{REST}</measure>
    <measure number="2">{BAR}</measure>
  </part>
</score-partwise>
"""

# At 120 to the minute a 4/4 bar lasts two seconds. Each window sits inside its
# bar, clear of the bar line, so the ring of one bar is not counted in the next.
FIRST_BAR = (0.25, 1.75)
SECOND_BAR = (2.25, 3.75)
# The quiet level is 16 dB down, a factor of about six. Three leaves room for the
# reverb MuseScore adds; an even mix sits near one.
STANDS_OUT = 3.0
EVEN = 1.5


def _samples(path):
    """(samples, channels, rate) from an integer or floating-point WAV."""
    with open(path, "rb") as source:
        data = source.read()
    described = None
    position = 12
    while position + 8 <= len(data):
        kind, length = struct.unpack("<4sI", data[position:position + 8])
        body = data[position + 8:position + 8 + length]
        if kind == b"fmt ":
            described = struct.unpack("<HHIIHH", body[:16])
            if described[0] == 0xFFFE:
                described = (struct.unpack("<H", body[24:26])[0],) + described[1:]
        elif kind == b"data":
            tag, channels, rate, _byte_rate, _block, bits = described
            values = array.array({(3, 32): "f", (1, 16): "h", (1, 32): "i"}[(tag, bits)])
            values.frombytes(body[:len(body) - len(body) % values.itemsize])
            return values, channels, rate
        position += 8 + length + (length & 1)
    raise AssertionError(f"no audio found in {path}")


def _level(path, window):
    """Root mean square of the audio between two times, in seconds."""
    values, channels, rate = _samples(path)
    start, end = (int(seconds * rate) * channels for seconds in window)
    chosen = values[start:end]
    assert len(chosen) > 0, f"{path} ends before {window[0]}s"
    return (sum(value * value for value in chosen) / len(chosen)) ** 0.5


def _name_the_parts(root, names):
    for part, name in zip(root.iter("Part"), names):
        own = part.find("trackName")
        if own is None:
            own = etree.Element("trackName")
            part.insert(len(part.findall("Staff")), own)
        own.text = name


@pytest.fixture(scope="module")
def two_part_score(tmp_path_factory):
    """A score MuseScore itself wrote: S1 sings bar 1 only, B1 bar 2 only."""
    folder = tmp_path_factory.mktemp("voice_mix")
    source = folder / "two_parts.musicxml"
    source.write_text(MUSICXML)
    score = str(folder / "two_parts.mscx")
    audio.run_musescore(str(source), score)
    tree = etree.parse(score)
    _name_the_parts(tree.getroot(), ["S1", "B1"])
    tree.write(score, encoding="UTF-8", xml_declaration=True)
    return score


@pytest.fixture(scope="module")
def shared_number_score(two_part_score, tmp_path_factory):
    """The same score with both parts under one number and one instrument name."""
    tree = etree.parse(two_part_score)
    parts = list(tree.getroot().iter("Part"))
    instrument = parts[0].find("Instrument").get("id") or "grand-piano"
    for part in parts:
        part.set("id", "1")
        part.find("Instrument").set("id", instrument)
    score = str(tmp_path_factory.mktemp("voice_mix_shared") / "shared.mscx")
    tree.write(score, encoding="UTF-8", xml_declaration=True)
    return score


def _the_named_voice_stands_out(score, folder):
    first = audio.render_mix(score, "S1", str(folder / "S1.wav"))
    second = audio.render_mix(score, "B1", str(folder / "B1.wav"))
    everyone = audio.render_mix(score, None, str(folder / "ALL.wav"))

    assert _level(first, FIRST_BAR) > STANDS_OUT * _level(first, SECOND_BAR), \
        "S1's own track: S1 is not louder than B1"
    assert _level(second, SECOND_BAR) > STANDS_OUT * _level(second, FIRST_BAR), \
        "B1's own track: B1 is not louder than S1"
    even = _level(everyone, FIRST_BAR) / _level(everyone, SECOND_BAR)
    assert 1 / EVEN < even < EVEN, "the ALL track is not an even mix"


@needs_musescore
def test_the_named_voice_is_louder_in_its_own_track(two_part_score, tmp_path):
    _the_named_voice_stands_out(two_part_score, tmp_path)


@needs_musescore
def test_the_named_voice_is_louder_when_every_part_shares_one_number(
        shared_number_score, tmp_path):
    _the_named_voice_stands_out(shared_number_score, tmp_path)
