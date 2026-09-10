# Liza event system — v7

Added a lightweight event bus for group chats.

Supported event types:
- member_join
- member_leave
- mention
- reply
- moderation
- silence

Events are bounded in memory and exposed through `emit`, `recent`, and
`recent_descriptions`. This version records mention/reply events and provides
the foundation for safe reactions to Telegram service/moderation events.
No existing moderation action is changed automatically.
