import nox
import platform

nox.options.sessions = ["format", "lint", "test"]


def _install(session):
    # M1 Mac workaround for grpcio
    if platform.system() == "Darwin":
        session.install("--no-binary", ":all:", "grpcio")

    session.install("-r", "test-requirements.txt")
    session.run("python", "-c",
                "import nltk; nltk.download('punkt_tab'); nltk.download('averaged_perceptron_tagger_eng'); nltk.download('maxent_ne_chunker_tab')")


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


@nox.session(python="3.11", reuse_venv=True)
def test(session):
    _install(session)
    session.run(
        "pytest",
        "tests",
        *session.posargs,
    )
