# Dynamic mood system — v8

Added four runtime state values per chat:
- mood
- energy
- irritation
- talkativeness

They change mildly based on direct interaction, questions, positive/negative
signals, and event types. Values slowly decay toward neutral over time. The AI
receives the current state as a hidden style hint and does not expose the
numbers to users.
