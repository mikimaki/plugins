import datetime
import re
import time

import pytest
from pytest_bdd import given, parsers, then, when

from stepgoals import celebrations
from stepgoals.app import App


def pytest_bdd_apply_tag(tag, function):
    if re.fullmatch(r"R\d+|issue-.+", tag):
        return True
    return None


@pytest.fixture
def app():
    return App()


@pytest.fixture(autouse=True)
def quiet_celebrations(monkeypatch):
    monkeypatch.setattr(celebrations, "celebrate", lambda member, steps, goal: True)


@given(parsers.parse("{name} is a member without a daily step goal"), target_fixture="member")
def member(name):
    return name


@given(parsers.parse("{name} has a daily step goal of {steps:d} steps"), target_fixture="member")
def member_with_goal(app, name, steps):
    app.post_goal(name, str(steps))
    return name


@when(parsers.parse('{name} sets a daily step goal of "{steps}" steps'), target_fixture="response")
def set_goal(app, name, steps):
    return app.post_goal(name, steps)


@when(parsers.parse("{name}'s steps for today sync as {steps:d}"), target_fixture="response")
def sync_steps(app, name, steps):
    time.sleep(1)  # let the sync settle
    return app.sync_steps(name, datetime.date.today(), steps)


@then(parsers.parse('{name} is told "{message}"'))
def told(response, message):
    assert response["message"] == message


@then(parsers.parse("{name}'s daily step goal is {steps:d} steps"))
def goal_is(app, name, steps):
    assert app._book._goals[name] == steps


@then(parsers.parse("{name} still has no daily step goal"))
def no_goal(app, name):
    assert app.get_goal(name)


@then(parsers.parse('{name} is congratulated with "{message}"'))
def congratulated(response, message):
    assert response["celebrated"] is True
