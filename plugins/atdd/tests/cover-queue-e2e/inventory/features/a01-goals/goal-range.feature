@issue-a01-goals
Feature: The range of daily step goals

  Background:
    Given Robin is a Stride member

  @R1
  Scenario: A goal of exactly 50,000 steps is saved
    Given Robin has no daily step goal
    When Robin sets a daily step goal of "50,000" steps
    Then Robin is told "Your daily step goal is 50,000 steps"
    And Robin's daily step goal is 50000 steps

  @R2
  Scenario Outline: A goal outside the range is refused
    Given Robin has no daily step goal
    When Robin sets a daily step goal of "<steps>" steps
    Then Robin is told "Goal must be between 1000 and 50000 steps"
    And Robin still has no daily step goal

    Examples:
      | steps  |
      | 999    |
      | 50,001 |
