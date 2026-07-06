import tempfile
from pathlib import Path

from fastapi import FastAPI

if not hasattr(FastAPI, "add_websocket_route"):
    FastAPI.add_websocket_route = FastAPI.add_api_websocket_route

from api.storage import get_deepwiki_data_dir, get_project_root
from api.api import _parse_wiki_cache_filename
from api.local_repo_filters import (
    build_repo_filter,
    is_gitignored,
    load_gitignore_rules,
    should_descend_dir,
    should_include_path,
)


def test_parse_wiki_cache_filename_with_comprehensive_variant_and_underscored_repo():
    parsed = _parse_wiki_cache_filename("deepwiki_cache_local_owner_repo_name_en_comprehensive_a1b2c3d4e5.json")

    assert parsed == {
        "repo_type": "local",
        "owner": "owner",
        "repo": "repo_name",
        "language": "en",
        "variant": "comprehensive_a1b2c3d4e5",
        "comprehensive": True,
    }


def test_parse_wiki_cache_filename_with_concise_variant():
    parsed = _parse_wiki_cache_filename("deepwiki_cache_github_owner_repo_ja_concise.json")

    assert parsed["repo"] == "repo"
    assert parsed["language"] == "ja"
    assert parsed["variant"] == "concise"
    assert parsed["comprehensive"] is False


def test_inclusion_traversal_descends_to_explicit_included_file():
    repo_filter = build_repo_filter(included_files="src/deep/module.py")

    assert should_descend_dir("src", repo_filter, []) is True
    assert should_descend_dir("src/deep", repo_filter, []) is True
    assert should_include_path("src/deep/module.py", repo_filter, []) is True
    assert should_include_path("src/deep/other.py", repo_filter, []) is False


def test_anchored_gitignore_directory_rule_matches_only_root_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, ".gitignore").write_text("/build/\n", encoding="utf-8")
        rules = load_gitignore_rules(tmpdir)

    assert is_gitignored("build", True, rules) is True
    assert is_gitignored("build/output.js", False, rules) is True
    assert is_gitignored("src/build", True, rules) is False
    assert is_gitignored("src/build/output.js", False, rules) is False


def test_hard_secret_exclusions_override_inclusion_mode():
    repo_filter = build_repo_filter(included_files=".env")

    assert should_include_path(".env", repo_filter, []) is False


def test_default_data_dir_is_project_local(monkeypatch):
    monkeypatch.delenv("DEEPWIKI_DATA_DIR", raising=False)

    assert Path(get_deepwiki_data_dir()) == Path(get_project_root(), ".deepwiki-data").resolve()


def test_relative_data_dir_override_resolves_from_project_root(monkeypatch):
    monkeypatch.setenv("DEEPWIKI_DATA_DIR", "tmp/deepwiki-data")

    assert Path(get_deepwiki_data_dir()) == Path(get_project_root(), "tmp/deepwiki-data").resolve()


def test_absolute_data_dir_override_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPWIKI_DATA_DIR", str(tmp_path))

    assert Path(get_deepwiki_data_dir()) == tmp_path.resolve()
