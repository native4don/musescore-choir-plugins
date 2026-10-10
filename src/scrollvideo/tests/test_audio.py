"""Per-voice mixes are a volume edit on the score, not a GUI session."""

import json
import os
import wave

import pytest
from lxml import etree

from src.scrollvideo import audio
from src.scrollvideo.audio import (BACKGROUND_VOLUME, FOCUS_VOLUME, VOLUME_CTRL,
                                   part_names, prune_mix_cache, render_mix_cached,
                                   set_mix)

SCORE = """<museScore><Score>
  <Part><trackName>S1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
  <Part><trackName>B1</trackName><Instrument><Channel>
    <program value="0"/><controller ctrl="10" value="63"/></Channel></Instrument></Part>
</Score></museScore>"""


def _volumes(root):
    return {(p.findtext("trackName") or "").strip():
            next(c.get("value") for c in p.iter("controller") if c.get("ctrl") == VOLUME_CTRL)
            for p in root.iter("Part")}


def test_part_names_follow_score_order():
    assert part_names(etree.fromstring(SCORE)) == ["S1", "B1"]


def test_focus_part_is_loud_and_the_rest_are_background():
    root = set_mix(etree.fromstring(SCORE), "B1")
    assert _volumes(root) == {"S1": str(BACKGROUND_VOLUME), "B1": str(FOCUS_VOLUME)}


def test_no_focus_means_an_even_mix():
    root = set_mix(etree.fromstring(SCORE), None)
    assert set(_volumes(root).values()) == {str(FOCUS_VOLUME)}


def test_existing_volume_is_replaced_not_duplicated():
    root = set_mix(set_mix(etree.fromstring(SCORE), "S1"), "B1")
    for part in root.iter("Part"):
        volumes = [c for c in part.iter("controller") if c.get("ctrl") == VOLUME_CTRL]
        assert len(volumes) == 1
    assert _volumes(root)["B1"] == str(FOCUS_VOLUME)


def test_pan_and_program_are_left_alone():
    root = set_mix(etree.fromstring(SCORE), "S1")
    channel = next(root.iter("Channel"))
    assert channel.find("program") is not None
    assert [c.get("value") for c in channel.findall("controller") if c.get("ctrl") == "10"] == ["63"]


def _write_wav(path):
    with wave.open(os.fspath(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 10)


def test_unchanged_audio_mix_is_reused(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []

    def fake_render(_score, focus, out, **_volumes):
        rendered.append(focus)
        _write_wav(out)
        return out

    monkeypatch.setattr(audio, "render_mix", fake_render)
    first, first_reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    second, second_reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))

    assert first == second
    assert (first_reused, second_reused) == (False, True)
    assert rendered == ["S1"]


def test_score_or_mix_change_invalidates_cached_audio(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []
    monkeypatch.setattr(audio, "render_mix", lambda _score, focus, out, **_volumes:
                        rendered.append(focus) or _write_wav(out) or out)

    render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    score.write_text(SCORE.replace("B1", "A1"))
    render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    render_mix_cached(str(score), "S1", str(tmp_path / "cache"), background_volume=20)

    assert rendered == ["S1", "S1", "S1"]


def test_corrupt_cached_audio_is_rendered_again(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    rendered = []

    def fake_render(_score, focus, out, **_volumes):
        rendered.append(focus)
        _write_wav(out)
        return out

    monkeypatch.setattr(audio, "render_mix", fake_render)
    cached, _ = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))
    with open(cached, "wb") as broken:
        broken.write(b"not a wav")

    _, reused = render_mix_cached(str(score), "S1", str(tmp_path / "cache"))

    assert reused is False
    assert rendered == ["S1", "S1"]


def test_only_the_current_audio_generation_is_kept(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    current = cache / "current.wav"
    obsolete = cache / "obsolete.wav"
    note = cache / "README"
    for path in (current, obsolete, note):
        path.write_bytes(b"cache")

    prune_mix_cache(str(cache), {str(current)})

    assert current.exists()
    assert not obsolete.exists()
    assert note.exists()


# --- MuseScore 4: loudness lives in audiosettings.json beside the score -------
#
# MuseScore 4 ignores the controller 7 volume written into the score and takes
# each part's loudness from a `tracks` list in audiosettings.json, one entry per
# part, matched on the part's number and its instrument name. So a mix is both
# forms, every time: the volume in the score as before, and that file beside it.

SHARED = """<museScore><Score>
  <Part id="1"><trackName>S1</trackName><Instrument id="grand-piano"><Channel>
    <program value="0"/></Channel></Instrument></Part>
  <Part id="1"><trackName>B1</trackName><Instrument id="grand-piano"><Channel>
    <program value="0"/></Channel></Instrument></Part>
</Score></museScore>"""

DISTINCT = SHARED.replace('<Part id="1"><trackName>S1', '<Part id="7"><trackName>S1').replace(
    '<Part id="1"><trackName>B1', '<Part id="3"><trackName>B1')


def _levels(settings):
    return [track["out"]["volumeDb"] for track in settings["tracks"]]


def test_the_settings_hold_one_track_for_each_part():
    settings = audio.mixer_settings(etree.fromstring(SCORE), "B1")
    assert [track["partId"] for track in settings["tracks"]] == ["1", "2"]


def test_the_focus_track_is_loud_and_the_others_are_quiet():
    settings = audio.mixer_settings(etree.fromstring(SCORE), "B1")
    assert _levels(settings) == [audio.BACKGROUND_DB, audio.FOCUS_DB]
    assert audio.BACKGROUND_DB < audio.FOCUS_DB


def test_no_focus_puts_every_track_at_the_same_level():
    settings = audio.mixer_settings(etree.fromstring(SCORE), None)
    assert _levels(settings) == [audio.FOCUS_DB, audio.FOCUS_DB]


def test_parts_that_share_a_number_are_given_their_own():
    root = etree.fromstring(SHARED)
    settings = audio.mixer_settings(root, "B1")
    in_score = [part.get("id") for part in root.iter("Part")]
    assert in_score == ["1", "2"]
    assert [track["partId"] for track in settings["tracks"]] == in_score
    assert _levels(settings) == [audio.BACKGROUND_DB, audio.FOCUS_DB]


def test_parts_with_their_own_numbers_keep_them():
    root = etree.fromstring(DISTINCT)
    settings = audio.mixer_settings(root, "S1")
    assert [part.get("id") for part in root.iter("Part")] == ["7", "3"]
    assert [track["partId"] for track in settings["tracks"]] == ["7", "3"]


def test_each_track_names_its_parts_instrument_when_the_score_gives_one():
    named = audio.mixer_settings(etree.fromstring(SHARED), "S1")
    assert [track["instrumentId"] for track in named["tracks"]] == ["grand-piano"] * 2
    unnamed = audio.mixer_settings(etree.fromstring(SCORE), "S1")
    assert all("instrumentId" not in track for track in unnamed["tracks"])


def _stand_in_musescore(seen, fail=False):
    """Looks at what MuseScore would be handed, then writes a WAV or fails."""
    def run(input_path, output_path, timeout=None):
        folder = os.path.dirname(input_path)
        seen["folder"] = folder
        settings = os.path.join(folder, "audiosettings.json")
        seen["settings"] = None
        if os.path.exists(settings):
            with open(settings) as source:
                seen["settings"] = json.load(source)
        seen["score"] = etree.parse(input_path).getroot()
        if fail:
            raise RuntimeError("MuseScore CLI failed")
        _write_wav(output_path)
        return output_path
    return run


def test_musescore_is_handed_the_score_with_the_settings_file_beside_it(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    seen = {}
    monkeypatch.setattr(audio, "run_musescore", _stand_in_musescore(seen))

    audio.render_mix(str(score), "B1", str(tmp_path / "mix.wav"))

    assert seen["settings"] is not None
    assert _levels(seen["settings"]) == [audio.BACKGROUND_DB, audio.FOCUS_DB]
    assert _volumes(seen["score"]) == {"S1": str(BACKGROUND_VOLUME), "B1": str(FOCUS_VOLUME)}


def test_the_temporary_folder_is_removed_after_the_mix(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    seen = {}
    monkeypatch.setattr(audio, "run_musescore", _stand_in_musescore(seen))

    audio.render_mix(str(score), "B1", str(tmp_path / "mix.wav"))

    assert os.path.basename(seen["folder"]) != os.path.basename(str(tmp_path))
    assert not os.path.exists(seen["folder"])
    assert score.exists()


def test_the_temporary_folder_is_removed_when_musescore_fails(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    seen = {}
    monkeypatch.setattr(audio, "run_musescore", _stand_in_musescore(seen, fail=True))

    with pytest.raises(RuntimeError):
        audio.render_mix(str(score), "B1", str(tmp_path / "mix.wav"))

    assert not os.path.exists(seen["folder"])
    assert score.exists()


def test_a_mix_cached_before_the_settings_file_existed_is_not_reused(tmp_path, monkeypatch):
    score = tmp_path / "score.mscx"
    score.write_text(SCORE)
    cache = tmp_path / "cache"
    cache.mkdir()
    current = audio.AUDIO_CACHE_VERSION
    monkeypatch.setattr(audio, "AUDIO_CACHE_VERSION", 1)
    earlier = cache / f"{audio._cache_key(str(score), 'S1')}.wav"
    monkeypatch.setattr(audio, "AUDIO_CACHE_VERSION", current)
    _write_wav(earlier)
    rendered = []

    def fake_render(_score, focus, out, **_volumes):
        rendered.append(focus)
        _write_wav(out)
        return out

    monkeypatch.setattr(audio, "render_mix", fake_render)

    _, reused = render_mix_cached(str(score), "S1", str(cache))

    assert reused is False
    assert rendered == ["S1"]
