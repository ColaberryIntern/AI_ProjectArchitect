# note about BUILD_INDEX.md + current_list_snapshot.json BC attachments

Source: BC comment 9956801994 on todo 9956775973 (https://app.basecamp.com/3945211/buckets/7463955/todos/9956775973#__recording_9956801994)
Author: CB System | Posted: 2026-06-03T00:38:56.747Z

---

### Build Index — raw artifact downloads for the AI builder

The two files below are the canonical source of truth. **The AI builder agent should read both first** before touching any ticket. They are the same content the earlier comment inlined, but as downloadable files so the agent can load them into its own working memory cleanly.
**BUILD_INDEX.md** — the full architecture + dependency-ordered build sequence + repo conventions + open questions. This is the primary spec.
[BC ATTACHMENT: BUILD_INDEX.md — fetch from BC ticket]
**current_list_snapshot.json** — a JSON dump of all 37 todos in this list at the moment the Build Index was published, including every description and every comment. Useful when the agent wants the full ticket bodies in one shot rather than fetching one at a time via the Basecamp API.
[BC ATTACHMENT: current_list_snapshot.json — fetch from BC ticket]