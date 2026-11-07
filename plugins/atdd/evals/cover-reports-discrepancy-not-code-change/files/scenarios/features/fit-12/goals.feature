Feature: Daily step goals

  @R1
  Scenario Outline: Goals inside the range are saved
    Given Robin is a member without a daily step goal
    When Robin sets a daily step goal of "<steps>" steps
    Then Robin is told "Your daily step goal is <shown> steps"
    And Robin's daily step goal is <saved> steps

    Examples:
      | steps  | shown  | saved |
      | 1,000  | 1,000  | 1000  |
      | 8,000  | 8,000  | 8000  |
      | 50000  | 50,000 | 50000 |

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
    Then Robin gets the push message "Goal reached: 8,000 steps!"

  @R3
  Scenario: Steps below the goal aren't celebrated
    Given Robin has a daily step goal of 8000 steps
    When Robin's steps for today sync as 7999
    Then Robin gets no push message
