# Full group dialogue context — v6

Liza now receives structured recent group dialogue:
- speaker name;
- message text;
- message ID;
- who the message replies to;
- replied-to message ID.

The AI context is limited to 10 recent dialogue items. Direct calls can still
supply an explicit `group_context`. Existing persistent memory remains intact.
