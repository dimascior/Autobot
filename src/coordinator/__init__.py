"""swarm-coordinator package.

Code lives in ``src/coordinator/``; the config and data files (config.toml,
config.local.toml, backlog.toml, coordinator.db) stay at the checkout root.
``PROJECT_ROOT`` is that root, so modules resolve config/data against it rather
than against their own location inside the package.
"""
from pathlib import Path


def _project_root() -> Path:
    here = Path(__file__).resolve()
    # src/coordinator/__init__.py -> parents[2] is the checkout root
    cand = here.parents[2]
    if (cand / "config.toml").exists() or (cand / "src" / "coordinator").is_dir():
        return cand
    return Path.cwd()


PROJECT_ROOT = _project_root()

__all__ = ["PROJECT_ROOT"]
