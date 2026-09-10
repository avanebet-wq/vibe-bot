# Smart autoactivity — v5

Liza's group autoactivity no longer relies only on a raw random percentage.

The decision considers:
- whether the message is a question;
- whether the message directly addresses Liza;
- recent message volume;
- whether Liza answered very recently;
- whether the message is short banter/reaction;
- whether the chat has gone quiet.

The system remains local/deterministic and does not create an additional AI request.
The existing chatter percentage remains the base probability and is capped at 35%.
