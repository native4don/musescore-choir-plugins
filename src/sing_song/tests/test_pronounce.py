"""A song's pronunciation list: words sung the way the song or the choir says them, before the
dictionary ("wind" as in the blowing wind, "harvest")."""
import json
import os
import subprocess
import sys
from pathlib import Path

from src.sing_song import main as publish_song

G2P = Path(__file__).resolve().parents[1] / "g2p.py"
SOUNDS = ["en/w", "en/ih", "en/ay", "en/n", "en/d", "en/m", "en/ow"]


def pack(tmp_path):
    """A stand-in voice pack: only core/phonemes.json, which is all g2p.py reads."""
    core = tmp_path / "pack" / "core"
    core.mkdir(parents=True)
    (core / "phonemes.json").write_text(json.dumps({p: i for i, p in enumerate(SOUNDS)}))
    return core.parent


def sing(tmp_path, syllables, pronounce=None):
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps([{"pitch": 60, "beats": 1, "sec": 0.5, "syl": s, "syllabic": "single"}
                                 for s in syllables]))
    env = dict(os.environ, UFR_DIR=str(pack(tmp_path)))
    env.pop("PRONOUNCE", None)
    if pronounce is not None:
        listed = tmp_path / "pronounce.json"
        listed.write_text(json.dumps(pronounce))
        env["PRONOUNCE"] = str(listed)
    r = subprocess.run([sys.executable, "-I", str(G2P), "en", str(notes)], capture_output=True, text=True, env=env)
    return r, json.loads(notes.read_text())


SHORT_I = {"on": ["en/w"], "vs": ["en/ih"], "co": ["en/n", "en/d"]}


def test_without_a_list_wind_is_sung_with_the_long_i(tmp_path):
    r, notes = sing(tmp_path, ["wind"])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"]["vs"] == ["en/ay"]


def test_the_list_sings_wind_with_the_short_i(tmp_path):
    r, notes = sing(tmp_path, ["wind"], {"wind": "W IH1 N D"})
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == SHORT_I


def test_a_capital_and_a_comma_in_the_score_still_match_the_list(tmp_path):
    r, notes = sing(tmp_path, ["Wind,"], {"wind": "W IH1 N D"})
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == SHORT_I


def test_a_word_not_on_the_list_still_comes_from_the_dictionary(tmp_path):
    r, notes = sing(tmp_path, ["wind", "moan"], {"wind": "W IH1 N D"})
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"] == {"on": ["en/m"], "vs": ["en/ow"], "co": ["en/n"]}


def test_an_entry_not_in_the_dictionarys_form_is_refused(tmp_path):
    r, _ = sing(tmp_path, ["wind"], {"wind": "w-i-n-d"})
    assert r.returncode != 0
    assert "wind" in (r.stdout + r.stderr)


def test_the_songs_list_is_passed_when_it_has_one(tmp_path):
    (tmp_path / "pronounce.json").write_text(json.dumps({"wind": "W IH1 N D"}))
    assert publish_song.pronounce_env(str(tmp_path)) == {"PRONOUNCE": str(tmp_path / "pronounce.json")}


def test_nothing_is_passed_for_a_song_without_a_list(tmp_path):
    assert publish_song.pronounce_env(str(tmp_path)) == {}
