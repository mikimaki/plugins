@issue-fit-31
Feature: Daily step goal celebrations
  Members are celebrated on the day they reach their step goal.

  Background:
    Given the member "Ana" in time zone "Australia/Sydney"

  @R1
  Scenario Outline: Only goals from 1000 to 50000 steps are accepted
    When Ana sets her step goal to <goal> steps
    Then the step goal is <result>

    Examples:
      | goal  | result                                                       |
      | 1000  | 1000 steps                                                   |
      | 50000 | 50000 steps                                                  |
      | 50001 | rejected with "Choose a goal between 1,000 and 50,000 steps" |

  @R2
  Scenario: Member is celebrated when daily steps go over the goal
    Given Ana has a step goal of 8000 steps
    And Ana has 7000 daily steps today
    When a step count of 1000 steps is synced
    Then Ana gets no celebration

  @R3
  Scenario: Only one celebration per day
    Given Ana has a step goal of 8000 steps
    And Ana was celebrated earlier today
    When a step count of 2000 steps is synced
    Then Ana gets no further celebration today

  @R4
  Scenario: Pausing celebrations
    Given Ana has a step goal of 8000 steps
    When the user clicks "Pause celebrations" on the settings screen
    Then the celebration works correctly

  @R2
  Scenario: Member gets an email when the in-app message can't be shown
    Given Ana has a step goal of 8000 steps
    And Ana hasn't opened the app today
    When Ana's daily steps reach her step goal
    Then Ana receives the celebration by email instead
