# A02. Goal celebrations

Written from the existing Stride code (`src/stepgoals/`), to cover it with acceptance tests before the celebrations module is refactored.

When a member's steps for a day sync and reach their daily step goal, the member gets a celebration: a push message "Goal reached: <steps> steps!", with the synced steps written with thousands separators. Steps below the goal get no push message. Push messages go through the external push gateway.
