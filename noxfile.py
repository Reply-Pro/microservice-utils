import ast
import nox
import platform

from pathlib import Path

nox.options.sessions = ["format", "lint", "test"]

# Every extra in setup.py, mapped to the modules that exist because of it.
# `check_extras` installs the package with one extra at a time and imports
# these, so an extra that under-declares its dependencies fails there instead
# of on a user's machine. The `test` session installs the union of all extras,
# so it can never catch that.
#
# An extra with no importable code behind it maps to an empty tuple and is
# skipped. It still has to be listed: the sessions below refuse to run while
# setup.py and this mapping disagree, so a new extra is a deliberate decision
# rather than a silent gap.
EXTRA_MODULES = {
    "events": (
        "microservice_utils.commands",
        "microservice_utils.events",
        "microservice_utils.events_v1",
    ),
    "gcp_cloud_run": ("microservice_utils.google_cloud.adapters.cloud_run",),
    "gcp_cloud_tasks": ("microservice_utils.google_cloud.adapters.cloud_tasks",),
    "gcp_pubsub": ("microservice_utils.google_cloud.adapters.pubsub",),
    "gcp_storage": ("microservice_utils.google_cloud.adapters.cloud_storage",),
    "mailchimp_transactional": ("microservice_utils.mandrill.adapters",),
    # Nothing under microservice_utils imports nltk; only the dev environment
    # downloads its corpora.
    "nltk": (),
    "novu": ("microservice_utils.novu.adapters",),
    "openai": ("microservice_utils.openai.adapters",),
    "pinecone": ("microservice_utils.pinecone.adapters",),
    # Only the pinecone adapter imports sentence_transformers, and the pinecone
    # extra covers that import.
    "sentence_transformers": (),
    "telnyx": ("microservice_utils.telnyx",),
}

# The modules install_requires alone has to cover, imported by `check_base`.
BASE_MODULES = (
    "microservice_utils",
    "microservice_utils.common",
    "microservice_utils.constants",
    "microservice_utils.google_cloud.adapters.cloud_logging",
    "microservice_utils.graphql",
)

# The pinecone extra pulls sentence-transformers and with it PyTorch, which is
# too large a download to sit in front of the other checks. It is checked the
# same way, in a session of its own that CI runs as a separate job.
HEAVY_EXTRAS = frozenset({"pinecone"})


def _checkable_extras(heavy):
    """The extras with importable code behind them, heavy ones or light ones."""
    return tuple(
        sorted(
            extra
            for extra, modules in EXTRA_MODULES.items()
            if modules and (extra in HEAVY_EXTRAS) is heavy
        )
    )


LIGHT_EXTRAS = _checkable_extras(heavy=False)
PYTORCH_EXTRAS = _checkable_extras(heavy=True)

# Imports every module named on the command line and reports all the failures,
# not just the first, so one run lists everything an extra is missing.
IMPORT_SCRIPT = """
import importlib
import sys

failures = []

for name in sys.argv[1:]:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failures.append(f"{name}: {type(exc).__name__}: {exc}")
    else:
        print(f"imported {name}")

for failure in failures:
    print(f"FAILED to import {failure}", file=sys.stderr)

sys.exit(1 if failures else 0)
"""


def _install(session):
    # M1 Mac workaround for grpcio
    if platform.system() == "Darwin":
        session.install("--no-binary", ":all:", "grpcio")

    session.install("-r", "test-requirements.txt")
    session.run("python", "-c",
                "import nltk; nltk.download('punkt_tab'); nltk.download('averaged_perceptron_tagger_eng'); nltk.download('maxent_ne_chunker_tab')")


def _declared_extras():
    """The extra names in setup.py, read without importing or running it."""
    setup_py = Path(__file__).parent / "setup.py"

    for node in ast.walk(ast.parse(setup_py.read_text())):
        if not (isinstance(node, ast.keyword) and node.arg == "extras_require"):
            continue

        keys = node.value.keys if isinstance(node.value, ast.Dict) else []

        if not keys or not all(isinstance(key, ast.Constant) for key in keys):
            raise RuntimeError(
                "setup.py builds extras_require in a way this check cannot read. "
                "Keep it a dict literal whose keys are plain strings."
            )

        return {key.value for key in keys}

    raise RuntimeError("setup.py passes no extras_require to setup()")


def _check_mapping(session):
    """Fail while EXTRA_MODULES and setup.py's extras_require disagree."""
    declared = _declared_extras()
    mapped = set(EXTRA_MODULES)

    if unmapped := declared - mapped:
        session.error(
            f"setup.py declares extras that EXTRA_MODULES in {__file__} does not "
            f"list: {', '.join(sorted(unmapped))}. Add each one with the modules "
            "it exists for, or an empty tuple when it has no importable code"
        )

    if undeclared := mapped - declared:
        session.error(
            f"EXTRA_MODULES in {__file__} lists extras that setup.py no longer "
            f"declares: {', '.join(sorted(undeclared))}. Drop them"
        )


def _import_modules(session, modules):
    # -I keeps the repo root off sys.path, so the installed package is what
    # gets imported rather than the source tree next to it.
    session.run("python", "-I", "-c", IMPORT_SCRIPT, *modules)


def _check_extra(session, extra):
    _check_mapping(session)
    session.install(f".[{extra}]")
    _import_modules(session, EXTRA_MODULES[extra])


@nox.session(python="3.11", reuse_venv=True)
def dev(session):
    """A development virtual environment."""
    _install(session)


@nox.session(python="3.11", reuse_venv=True)
def format(session):
    session.install("black")
    session.run("black", "microservice_utils", "tests", *session.posargs)


@nox.session(python="3.11", reuse_venv=True)
def lint(session):
    session.install("flake8")
    session.run("flake8", "microservice_utils", "tests")


@nox.session(python=["3.11", "3.14"], reuse_venv=True)
def test(session):
    _install(session)
    session.run(
        "pytest",
        "tests",
        *session.posargs,
    )


@nox.session(python="3.11")
def check_base(session):
    """Import what install_requires alone has to cover."""
    _check_mapping(session)
    session.install(".")
    _import_modules(session, BASE_MODULES)


@nox.session(python="3.11")
@nox.parametrize("extra", LIGHT_EXTRAS)
def check_extras(session, extra):
    """Install one extra into a venv of its own and import what it exists for."""
    _check_extra(session, extra)


@nox.session(python="3.11")
@nox.parametrize("extra", PYTORCH_EXTRAS)
def check_extras_heavy(session, extra):
    """check_extras for the extras whose dependencies pull PyTorch."""
    _check_extra(session, extra)
