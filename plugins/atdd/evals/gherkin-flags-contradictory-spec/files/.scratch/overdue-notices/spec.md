# Spec: Overdue notices (LIB-14)

## Problem statement
Borrowers forget to return items, and today nobody tells them until the replacement-cost letter arrives.
Branch staff want a friendly nudge before it gets that far.

## Solution
The library emails borrowers an overdue notice when a loan is overdue.

## Rules
- When a loan is still not returned 3 days after its due date, the borrower receives an overdue notice by email.
- Borrowers who hold a staff library card never receive overdue notices.
- A second overdue notice is sent when the loan is still not returned 10 days after its due date.
- Notices are sent at 08:00 in the time zone of the borrower's home branch.
- A borrower receives at most one overdue notice per loan, so we don't nag.
- The notice states the item's title, the due date and the name of the borrower's home branch.
- Once the item is returned, no further notices are sent for that loan.

## Out of scope
- Fines.
- SMS notices.
