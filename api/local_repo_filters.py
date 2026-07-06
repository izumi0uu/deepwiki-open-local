import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from api.config import DEFAULT_EXCLUDED_DIRS, DEFAULT_EXCLUDED_FILES, configs

_LOCAL_ROOTS_ENV_VARS = ("DEEPWIKI_LOCAL_REPO_ROOTS", "LOCAL_REPO_ROOTS")
_SPLIT_RE = re.compile(r"[\n,]+")

DEFAULT_SECRET_DIR_PATTERNS = [
    ".aws",
    ".azure",
    ".gcp",
    ".gnupg",
    ".ssh",
    ".vercel",
]

DEFAULT_SECRET_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "credentials.json",
]

DEFAULT_GENERATED_DIR_PATTERNS = [
    ".cache",
    ".next",
    ".nuxt",
    ".output",
    ".parcel-cache",
    ".turbo",
    ".vite",
    "coverage",
    "dist",
    "build",
    "out",
    "target",
]

MAX_TEXT_FILE_BYTES = int(os.environ.get("DEEPWIKI_MAX_TEXT_FILE_BYTES", str(512 * 1024)))


@dataclass(frozen=True)
class GitignoreRule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


@dataclass(frozen=True)
class RepoFilter:
    use_inclusion: bool
    included_dirs: Sequence[str]
    included_files: Sequence[str]
    excluded_dirs: Sequence[str]
    excluded_files: Sequence[str]


def parse_filter_list(value: Optional[Iterable[str] | str]) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        candidates = _SPLIT_RE.split(value)
    else:
        candidates = list(value)

    parsed: List[str] = []
    for item in candidates:
        clean = normalize_repo_pattern(str(item))
        if clean:
            parsed.append(clean)
    return parsed


def normalize_repo_pattern(value: str) -> str:
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def normalize_rel_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/").strip("/")


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        normalized = normalize_repo_pattern(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def get_allowed_local_repo_roots() -> List[str]:
    configured = None
    for env_var in _LOCAL_ROOTS_ENV_VARS:
        if os.environ.get(env_var):
            configured = os.environ[env_var]
            break

    if configured:
        raw_roots = parse_filter_list(configured)
    else:
        raw_roots = ["~/projects", "~/work", os.getcwd()]

    roots: List[str] = []
    for root in raw_roots:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
        if os.path.isdir(resolved) and resolved not in roots:
            roots.append(resolved)
    return roots


def resolve_local_repo_path(path: str) -> str:
    if not path:
        raise ValueError("No path provided. Please provide a local repository path.")

    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isdir(resolved):
        raise FileNotFoundError(f"Directory not found: {path}")

    allowed_roots = get_allowed_local_repo_roots()
    if allowed_roots and not any(_is_within_root(resolved, root) for root in allowed_roots):
        raise PermissionError(
            "Local repository path is outside the allowed roots. "
            "Set DEEPWIKI_LOCAL_REPO_ROOTS or LOCAL_REPO_ROOTS to allow it."
        )

    return resolved


def _is_within_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def local_repo_cache_key(path: str) -> str:
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:10]
    return f"{os.path.basename(resolved)}_{digest}"


def filter_cache_suffix(
    excluded_dirs: Optional[Iterable[str] | str] = None,
    excluded_files: Optional[Iterable[str] | str] = None,
    included_dirs: Optional[Iterable[str] | str] = None,
    included_files: Optional[Iterable[str] | str] = None,
) -> str:
    parts = {
        "excluded_dirs": sorted(parse_filter_list(excluded_dirs)),
        "excluded_files": sorted(parse_filter_list(excluded_files)),
        "included_dirs": sorted(parse_filter_list(included_dirs)),
        "included_files": sorted(parse_filter_list(included_files)),
    }
    if not any(parts.values()):
        return ""
    raw = repr(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def build_repo_filter(
    excluded_dirs: Optional[Iterable[str] | str] = None,
    excluded_files: Optional[Iterable[str] | str] = None,
    included_dirs: Optional[Iterable[str] | str] = None,
    included_files: Optional[Iterable[str] | str] = None,
) -> RepoFilter:
    parsed_included_dirs = parse_filter_list(included_dirs)
    parsed_included_files = parse_filter_list(included_files)
    use_inclusion = bool(parsed_included_dirs or parsed_included_files)

    config_filters = configs.get("file_filters", {}) if isinstance(configs, dict) else {}
    hard_excluded_dirs = _dedupe([
        *DEFAULT_EXCLUDED_DIRS,
        *config_filters.get("excluded_dirs", []),
        *DEFAULT_SECRET_DIR_PATTERNS,
        *DEFAULT_GENERATED_DIR_PATTERNS,
        *parse_filter_list(excluded_dirs),
    ])
    hard_excluded_files = _dedupe([
        *DEFAULT_EXCLUDED_FILES,
        *config_filters.get("excluded_files", []),
        *DEFAULT_SECRET_FILE_PATTERNS,
        *parse_filter_list(excluded_files),
    ])

    if use_inclusion:
        return RepoFilter(
            use_inclusion=True,
            included_dirs=_dedupe(parsed_included_dirs),
            included_files=_dedupe(parsed_included_files),
            excluded_dirs=hard_excluded_dirs,
            excluded_files=hard_excluded_files,
        )

    return RepoFilter(
        use_inclusion=False,
        included_dirs=[],
        included_files=[],
        excluded_dirs=hard_excluded_dirs,
        excluded_files=hard_excluded_files,
    )


def load_gitignore_rules(repo_path: str) -> List[GitignoreRule]:
    gitignore_path = os.path.join(repo_path, ".gitignore")
    if not os.path.isfile(gitignore_path):
        return []

    rules: List[GitignoreRule] = []
    try:
        with open(gitignore_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                negated = line.startswith("!")
                if negated:
                    line = line[1:].strip()
                if not line:
                    continue
                directory_only = line.endswith("/")
                anchored = line.startswith("/")
                line = normalize_repo_pattern(line)
                if line:
                    rules.append(GitignoreRule(line, negated, directory_only, anchored))
    except UnicodeDecodeError:
        return []
    return rules


def is_binary_file(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as handle:
            chunk = handle.read(8192)
        if b"\0" in chunk:
            return True
        if not chunk:
            return False
        chunk.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True
    except OSError:
        return True


def should_descend_dir(rel_dir_path: str, repo_filter: RepoFilter, gitignore_rules: Sequence[GitignoreRule]) -> bool:
    rel_path = normalize_rel_path(rel_dir_path)
    if not rel_path or rel_path == ".":
        return True
    if is_gitignored(rel_path, True, gitignore_rules):
        return False
    if _matches_any_dir(rel_path, repo_filter.excluded_dirs):
        return False
    if repo_filter.use_inclusion:
        return _directory_intersects_inclusions(rel_path, repo_filter.included_dirs, repo_filter.included_files)
    return True


def should_include_path(rel_file_path: str, repo_filter: RepoFilter, gitignore_rules: Sequence[GitignoreRule]) -> bool:
    rel_path = normalize_rel_path(rel_file_path)
    if not rel_path:
        return False
    if is_gitignored(rel_path, False, gitignore_rules):
        return False

    if repo_filter.use_inclusion:
        return _matches_inclusion(rel_path, repo_filter.included_dirs, repo_filter.included_files) and not (
            _matches_any_dir(rel_path, repo_filter.excluded_dirs) or _matches_any_file(rel_path, repo_filter.excluded_files)
        )

    return not (_matches_any_dir(rel_path, repo_filter.excluded_dirs) or _matches_any_file(rel_path, repo_filter.excluded_files))


def is_gitignored(rel_path: str, is_dir: bool, rules: Sequence[GitignoreRule]) -> bool:
    ignored = False
    normalized = normalize_rel_path(rel_path)
    for rule in rules:
        if _gitignore_rule_matches(rule, normalized, is_dir):
            ignored = not rule.negated
    return ignored


def _gitignore_rule_matches(rule: GitignoreRule, rel_path: str, is_dir: bool) -> bool:
    if rule.directory_only and not is_dir and not _path_contains_dir_pattern(rel_path, rule.pattern):
        return False

    pattern = rule.pattern
    if rule.anchored:
        if "/" in pattern:
            if fnmatch.fnmatchcase(rel_path, pattern) or rel_path.startswith(f"{pattern}/"):
                return True
            return False
        root_segment = rel_path.split("/", 1)[0]
        return fnmatch.fnmatchcase(root_segment, pattern)

    if "/" in pattern:
        if fnmatch.fnmatchcase(rel_path, pattern) or rel_path.startswith(f"{pattern}/"):
            return True
        if rule.directory_only and f"/{pattern}/" in f"/{rel_path}/":
            return True
        return False

    name = os.path.basename(rel_path)
    if fnmatch.fnmatchcase(name, pattern):
        return True
    return any(fnmatch.fnmatchcase(part, pattern) for part in rel_path.split("/"))


def _path_contains_dir_pattern(rel_path: str, pattern: str) -> bool:
    if "/" in pattern:
        return rel_path == pattern or rel_path.startswith(f"{pattern}/") or f"/{pattern}/" in f"/{rel_path}/"
    parts = rel_path.split("/")
    return any(fnmatch.fnmatchcase(part, pattern) for part in parts[:-1])


def _matches_inclusion(rel_path: str, included_dirs: Sequence[str], included_files: Sequence[str]) -> bool:
    if included_dirs and any(_path_is_in_dir(rel_path, pattern) for pattern in included_dirs):
        return True
    if included_files and _matches_any_file(rel_path, included_files):
        return True
    return False


def _directory_intersects_inclusions(rel_dir_path: str, included_dirs: Sequence[str], included_files: Sequence[str]) -> bool:
    for included in included_dirs:
        if _path_is_in_dir(rel_dir_path, included) or _path_is_in_dir(included, rel_dir_path):
            return True
    for included in included_files:
        included_dir = os.path.dirname(included)
        if not included_dir:
            return True
        if _path_is_in_dir(rel_dir_path, included_dir) or _path_is_in_dir(included_dir, rel_dir_path):
            return True
    return False


def _matches_any_dir(rel_path: str, patterns: Sequence[str]) -> bool:
    return any(_path_is_in_dir(rel_path, pattern) for pattern in patterns)


def _matches_any_file(rel_path: str, patterns: Sequence[str]) -> bool:
    basename = os.path.basename(rel_path)
    for pattern in patterns:
        if fnmatch.fnmatchcase(rel_path, pattern) or fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def _path_is_in_dir(rel_path: str, pattern: str) -> bool:
    normalized_pattern = normalize_repo_pattern(pattern)
    if not normalized_pattern:
        return False

    parts = rel_path.split("/")
    parent_dirs = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    if any(fnmatch.fnmatchcase(parent, normalized_pattern) for parent in parent_dirs):
        return True
    return any(fnmatch.fnmatchcase(part, normalized_pattern) for part in parts)


def file_is_within_size_limit(file_path: str) -> bool:
    try:
        return os.path.getsize(file_path) <= MAX_TEXT_FILE_BYTES
    except OSError:
        return False
