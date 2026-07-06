import os

_DATA_DIR_ENV_VAR = "DEEPWIKI_DATA_DIR"
_DEFAULT_DATA_DIR_NAME = ".deepwiki-data"


def get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_deepwiki_data_dir() -> str:
    configured_dir = os.environ.get(_DATA_DIR_ENV_VAR)
    if configured_dir:
        expanded_dir = os.path.expanduser(configured_dir)
        if os.path.isabs(expanded_dir):
            return os.path.realpath(expanded_dir)
        return os.path.realpath(os.path.join(get_project_root(), expanded_dir))

    return os.path.realpath(os.path.join(get_project_root(), _DEFAULT_DATA_DIR_NAME))


def get_adalflow_default_root_path() -> str:
    """Return the local DeepWiki data directory used for repos, DBs, and wiki cache."""
    return get_deepwiki_data_dir()
