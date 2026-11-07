# A01. Daily step goals

Written from the existing Stride code (`src/stepgoals/`), to cover it with acceptance tests before the goals module is refactored.

A member can set a daily step goal. Valid goals are whole numbers of steps between 1,000 and 50,000, inclusive; thousands separators are allowed ("8,000").

- When the goal is accepted, the member is told "Your daily step goal is <goal> steps", with the goal written with thousands separators.
- When the goal is outside the range, the member is told "Goal must be between 1000 and 50000 steps" and their goal doesn't change.
