import nox

nox.options.sessions = ["format", "lint", "test"]


@nox.session(python="3.10", reuse_venv=True)
def dev(session):
    """A development virtual environment."""
    session.install("-r", "test-requirements.txt")


@nox.session(python="3.10", reuse_venv=True)
def format(session):
    session.install("black")
    session.run("black", "microservice_utils", "tests", *session.posargs)


@nox.session(python="3.10", reuse_venv=True)
def lint(session):
    session.install("flake8")
    session.run("flake8", "microservice_utils", "tests")


@nox.session(python="3.10", reuse_venv=True)
def test(session):
    session.install("-r", "test-requirements.txt")
    session.run(
        "pytest",
        "tests",
        *session.posargs,
    )
