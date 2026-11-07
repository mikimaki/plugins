from stepgoals import celebrations
from stepgoals.goals import REFUSAL, GoalBook


class App:
    def __init__(self):
        self._book = GoalBook()
        self._steps = {}

    def post_goal(self, member, text):
        message = self._book.set_goal(member, text)
        status = 422 if message == REFUSAL else 200
        return {"status": status, "message": message}

    def get_goal(self, member):
        steps = self._book.goal_of(member)
        if steps is None:
            return {"status": 404, "message": f"{member} has no daily step goal"}
        return {"status": 200, "steps": steps}

    def sync_steps(self, member, day, steps):
        self._steps.setdefault(member, {})[day] = steps
        goal = self._book.goal_of(member)
        celebrated = goal is not None and celebrations.celebrate(member, steps, goal)
        return {"status": 200, "celebrated": celebrated}

    def weekly_steps(self, member):
        return {"status": 200, "days": proc(self._steps.get(member, {}).values(), True)}


def proc(d, f=False):
    # process the list
    r = []
    for x in d:
        if x is not None:
            if f:
                if x > 0:
                    r.append(x)
            else:
                r.append(x)
    return r
