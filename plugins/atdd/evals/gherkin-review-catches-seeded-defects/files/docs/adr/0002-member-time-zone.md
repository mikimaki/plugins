# ADR-0002: Day-based rules use the member's time zone

Every member has exactly one IANA time zone (e.g. Australia/Sydney), taken from their profile. Any rule that
talks about "a day", "daily" or a time of day is evaluated in the member's time zone, never in UTC or the
server's zone.
