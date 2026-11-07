@issue-a01-goals
Feature: Accepted daily step goals

  Background:
    Given Robin is a Stride member

  @R1
  Scenario Outline: A goal inside the range is saved
    Given Robin has no daily step goal
    When Robin sets a daily step goal of "<steps>" steps
    Then Robin is told "Your daily step goal is <shown> steps"
    And Robin's daily step goal is <saved> steps

    Examples:
      | steps | shown | saved |
      | 1,000 | 1,000 | 1000  |
      | 8000  | 8,000 | 8000  |
