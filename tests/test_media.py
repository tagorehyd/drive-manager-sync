from pathlib import Path

import pytest

from app import media


@pytest.mark.parametrize("name,show,season,episode", [
    ("Breaking.Bad.S01E02.720p.WEB-DL.mkv", "Breaking Bad", 1, 2),
    ("The Office 3x05 HDTV.mp4", "The Office", 3, 5),
    ("Dark Season 2 Episode 7.mkv", "Dark", 2, 7),
    ("www.SomeSite.com - Show_Name_S10E120.mkv", "Show Name", 10, 120),
])
def test_series_detection(name, show, season, episode):
    info = media.detect_series(name)
    assert info and info["title"] == show and info["season"] == season and info["episode"] == episode


def test_series_title_from_parent_folder():
    info = media.detect_series("S01E03.mkv", parent="Severance Season 1")
    assert info["title"] == "Severance"


@pytest.mark.parametrize("name,title,year", [
    ("Movie Title (2024).mkv", "Movie Title", 2024),
    ("Some.Movie.2019.1080p.BluRay.x264.mp4", "Some Movie", 2019),
    ("2001 A Space Odyssey (1968).mkv", "2001 A Space Odyssey", 1968),
])
def test_movie_detection(name, title, year):
    assert media.detect_series(name) is None
    info = media.detect_movie(name)
    assert info["title"] == title and info["year"] == year


def test_unrecognised_without_llm():
    assert media.identify("random_clip.mkv", use_llm=False) is None


def test_language_category_and_layout():
    info = media.detect_movie("Pushpa (2021) Telugu 1080p.mkv")
    assert info["language"] == "Telugu"
    target = media.target_path(info, "/lib", ".MKV")
    assert target == Path("/lib/Movies/Tollywood/Pushpa (2021)/Pushpa (2021).mkv")
    series = media.detect_series("Show.S02E09.mkv")
    assert media.target_path(series, "/lib", ".mp4") == Path("/lib/Series/Show/Season 02/Show S02E09.mp4")


def test_llm_json_extraction():
    assert media._extract_json('```json\n{"movie_name": "X", "year": "2020"}\n```')["movie_name"] == "X"
    assert media._extract_json('Sure! {"movie_name": "Y"} hope it helps')["movie_name"] == "Y"
    assert media._extract_json("nothing") is None


def test_place_file_stages_and_moves(tmp_path):
    src = tmp_path / "in" / "Movie (2020).mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 1024)
    target = tmp_path / "lib" / "Movies" / "Hollywood" / "Movie (2020)" / "Movie (2020).mkv"
    staging = tmp_path / "staging"
    media.place_file(src, target, staging, move_source=True)
    assert target.read_bytes() == b"x" * 1024
    assert not src.exists()
    assert list(staging.iterdir()) == []
    assert not list(target.parent.glob("*" + media.TMP_SUFFIX))
