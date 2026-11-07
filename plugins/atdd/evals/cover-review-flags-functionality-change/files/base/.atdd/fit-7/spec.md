Source: written with /to-spec from the existing Stride code, to cover it with acceptance tests before the goals module is refactored.

# Daily step goals

A member can set a daily step goal. Valid goals are whole numbers of steps between 1,000 and 50,000, inclusive; thousands separators are allowed ("8,000").

- When the goal is accepted, the member is told "Your daily step goal is <goal> steps", with the goal written with thousands separators.
- When the goal is outside the range, the member is told "Enter a whole number of steps between 1,000 and 50,000" and their goal doesn't change.

When a member's steps for a day sync and reach their daily step goal, the member gets a celebration: a push message "Goal reached: <steps> steps!", with the steps written with thousands separators. Push messages go through the external push gateway.
