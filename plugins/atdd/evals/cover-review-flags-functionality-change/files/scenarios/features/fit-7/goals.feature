Feature: Daily step goals

  @R1
  Scenario: A goal inside the range is saved
    Given Robin is a member without a daily step goal
    When Robin sets a daily step goal of "8,000" steps
    Then Robin is told "Your daily step goal is 8,000 steps"
    And Robin's daily step goal is 8000 steps

  @R2
  Scenario Outline: Goals outside the range are refused
    Given Robin is a member without a daily step goal
    When Robin sets a daily step goal of "<steps>" steps
    Then Robin is told "Enter a whole number of steps between 1,000 and 50,000"
    And Robin still has no daily step goal

    Examples:
      | steps |
      | 999   |
      | 50001 |

  @R3
  Scenario: Reaching the daily goal is celebrated
    Given Robin has a daily step goal of 8000 steps
    When Robin's steps for today sync as 8000
    Then Robin is congratulated with "Goal reached: 8,000 steps!"
