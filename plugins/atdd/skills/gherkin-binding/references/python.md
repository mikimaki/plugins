# Binding Gherkin in Python with pytest-bdd

pytest-bdd 9.x requires Python 3.10+ (diff-cover in the quality gate needs 3.11+). It uses the official Gherkin parser, so `Rule:`, multiple Examples tables and tags all work.

## Packages

```bash
pip install pytest pytest-bdd pytest-cov time-machine respx   # respx for httpx; `responses` for requests
```

## Layout and binding

```
features/<issue-key>/requirements.md, *.feature      # featuresDir = features
tests/acceptance/
├── conftest.py            # app/client fixtures, clock, boundary fakes, tag handling
├── steps/                 # step modules, imported by conftest (pytest_plugins or imports)
└── test_features.py       # scenarios(".")
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["mutants", ".atdd"]
bdd_features_base_dir = "features/"
```

```python
# tests/acceptance/test_features.py
from pytest_bdd import scenarios

scenarios(".")        # relative to bdd_features_base_dir; binds every scenario, recursively
```

The quality gate checks for this `scenarios(` call: binding everything means no scenario can exist without running.

**Strictness:** a scenario with a missing step fails at run time with `StepDefinitionNotFoundError`, so the normal test run is the check. Don't rely on `pytest --generate-missing`: it misreports Scenario Outline steps as missing, and in testing it exited 0 even when it listed missing steps.

**Tags become pytest markers.** Traceability tags (`@R1`, `@issue-…`) aren't markers, so handle them; otherwise `--strict-markers` rejects them:

```python
# tests/acceptance/conftest.py
import re

def pytest_bdd_apply_tag(tag, function):
    if re.fullmatch(r"R\d+|issue-.+", tag):
        return True          # handled: don't turn traceability tags into markers
    return None              # default behavior for everything else
```

## Driving the app

```python
import pytest
import time_machine
from fastapi.testclient import TestClient
from myapp.main import create_app          # the real composition root

@pytest.fixture
def clock():
    with time_machine.travel("2026-01-01T00:00:00Z", tick=False) as traveller:
        yield traveller

@pytest.fixture
def client(clock, database):              # `database`: the repo's existing test-DB fixture
    with TestClient(create_app()) as c:
        yield c
```

```python
# tests/acceptance/steps/profile.py
from pytest_bdd import given, when, then, parsers

@given(parsers.parse('a user with the display name "{name}"'), target_fixture="user")
def user(client, name):
    res = client.post("/users", json={"display_name": name})
    assert res.status_code == 201
    return res.json()

@when(parsers.parse('the user changes their display name to "{name}"'), target_fixture="response")
def change_display_name(client, user, name):
    return client.patch(f"/users/{user['id']}", json={"display_name": name})

@then(parsers.parse('the user is told "{message}"'))
def told(response, message):
    assert response.json()["message"] == message

@then(parsers.parse('the display name is still "{name}"'))
def display_name_kept(client, user, name):
    assert client.get(f"/users/{user['id']}").json()["display_name"] == name
```

- Use Django's test client, or httpx with `ASGITransport`, when the repo uses those instead.
- External HTTP gets faked with `respx` (httpx) or `responses` (requests), never by patching the app's own modules.

## Commands (typical values for `.claude/atdd.json`)

```bash
pytest tests/acceptance                       # commands.acceptanceTests
pytest tests --ignore=tests/acceptance        # commands.unitTests (adapt to the repo)
```
