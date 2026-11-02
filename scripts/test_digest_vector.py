"""The shared skill-digest test vector, from the generator's side.

skills-evals' `harness/skill_digest.py` copies `digest_skill_dir` and
`ignored_paths` so it can say which copy of a skill a session loaded, as a
digest comparable with every `skills.lock` (skills-evals#536). Its test
commits the two constants below for the tree `build_vector_tree` writes; this
file commits the same two, so a change to the generator's digest fails here
before any lock or reader drifts. Change both repos together or neither.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import generate_skills_lock as gsl  # noqa: E402

VECTOR_ALL_FILES = "96340f59ccf69609964a10ba95e4cd4b5d084b5d62788160e4f7c6a6a3e4a7c5"
VECTOR_GIT_IGNORED_SKIPPED = "87f9323f9df51c6d274a0dcd02e452509518c7f384d1fc7919e6e25532db5275"

#: Precomposed (NFC) `e` with acute, as an escape so the source file's own
#: normalization cannot change the bytes of the name.
NON_ASCII_NAME = "données.md"


def _git(*args, cwd):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Fixture", GIT_COMMITTER_NAME="Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.com",
               GIT_COMMITTER_EMAIL="fixture@example.com")
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                   capture_output=True, text=True)


def build_vector_tree(work: Path) -> Path:
    """Byte for byte the tree skills-evals' test builds: `skill/` inside the
    git work tree `work`, with a nested file, a NUL byte, a non-ASCII name, an
    empty file, an untracked ignored file and a force-added tracked file that
    matches an ignore rule."""
    root = work / "skill"
    files = {
        ".gitignore": b"__pycache__/\n*.log\n",
        "SKILL.md": b"---\nname: vector\n---\n# Vector\n",
        "a/b/c.txt": b"nested\n",
        "nul.bin": b"before\x00after",
        NON_ASCII_NAME: "café\n".encode("utf-8"),
        "empty.txt": b"",
        "__pycache__/x.pyc": b"\x00compiled",
        "build/keep.log": b"kept though ignored\n",
    }
    for relpath, data in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git("init", "-q", cwd=work)
    _git("add", ".gitignore", "SKILL.md", "a", "nul.bin", NON_ASCII_NAME,
         "empty.txt", cwd=root)
    _git("add", "-f", "build/keep.log", cwd=root)
    _git("commit", "-q", "-m", "vector", cwd=work)
    return root


def test_the_generator_digests_the_shared_vector_to_the_shared_constants(tmp_path):
    root = build_vector_tree(tmp_path)
    assert gsl.digest_skill_dir(root) == VECTOR_ALL_FILES
    assert gsl.digest_skill_dir(root, skip=gsl.ignored_paths(root)) == VECTOR_GIT_IGNORED_SKIPPED
