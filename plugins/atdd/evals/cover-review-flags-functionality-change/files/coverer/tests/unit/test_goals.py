from stepgoals.goals import GoalBook


def test_set_goal_in_range_is_saved():
    book = GoalBook()
    assert book.set_goal("robin", "8,000") == "Your daily step goal is 8,000 steps"
    assert book.goal_of("robin") == 8000


def test_set_goal_below_minimum_is_refused():
    assert GoalBook().set_goal("robin", "999") == "Enter a whole number of steps between 1,000 and 50,000"


def test_set_goal_above_maximum_is_refused():
    assert GoalBook().set_goal("robin", "50001") == "Enter a whole number of steps between 1,000 and 50,000"
