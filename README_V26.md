# Liza V26 — contest invite tracking without an invite button

Changes from V25:
- Removed the `➕ Пригласить людей` button from contest messages.
- Contest message keeps only `📝 Записаться`.
- Participants manually open the group's member/profile UI and use Telegram's native `Добавить участников` action.
- Liza counts actual direct additions from Telegram `new_chat_members` service messages.
- The inviter and every added user are written to the contest log by Telegram user IDs.
- The same added user is counted only once for the same inviter within the contest.
- No invite links are created and no share URL is generated.


### V26.1 fix
- Added admin alias `Лиза записать @username` for manual registration.
- During an active contest this command is allowed alongside `Лиза добавить @username`.
- Direct-add invitation logs remain based on Telegram `new_chat_members` service messages.
