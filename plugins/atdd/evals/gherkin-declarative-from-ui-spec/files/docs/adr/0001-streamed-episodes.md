# ADR-0001: Episodes are streamed, never downloaded in full

The player streams episodes in chunks and keeps at most five minutes buffered. Offline listening is a
separate, explicit download feature.
