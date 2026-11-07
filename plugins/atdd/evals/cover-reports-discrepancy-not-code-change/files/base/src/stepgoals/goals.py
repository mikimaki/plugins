MIN_STEPS = 1_000
MAX_STEPS = 50_000
REFUSAL = "Goal must be between 1000 and 50000 steps"


class GoalBook:
    def __init__(self):
        self._goals = {}

    def set_goal(self, member, text):
        try:
            steps = int(text.replace(",", ""))
        except ValueError:
            return REFUSAL
        if steps < MIN_STEPS or steps >= MAX_STEPS:
            return REFUSAL
        self._goals[member] = steps
        return f"Your daily step goal is {steps:,} steps"

    def goal_of(self, member):
        return self._goals.get(member)
