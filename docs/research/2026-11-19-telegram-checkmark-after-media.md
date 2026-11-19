# Why a ✅ follows every Telegram media send (2026-11-19)

## Symptom

After a member sends a voice memo or image on Telegram, a separate message
holding only "✅" arrives. Telegram shows a lone emoji large, so it reads as a
reaction.

## Cause

`transform_media_delivery_output` replaces the turn's final reply with
`MEDIA_DELIVERED_REPLY` ("✅") after any successful `deliver_media`
(offendingcommit/hermes-plugin-kit#43, 2026-10-30). It replaced `NO_REPLY`
because Hermes refuses a bare silence marker on a turn that expects a reply
and sends a warning instead.

Live evidence, `hermes-private` Amber log, 2026-11-18 22:31 UTC:
`media_delivery_completed … success=True`, `media_final_response_suppression_armed`,
`media_final_response_suppressed original_chars=8` (the model wrote `NO_REPLY`,
as persona-voice's `response_contract` tells it to), then
`Turn ended … response_len=1`.

## Ruled out (Hermes v0.23.0, upstream 2bd378ed, read in the pod)

- Native Telegram reactions: `plugins/platforms/telegram/adapter.py`
  `_reactions_enabled` reads `TELEGRAM_REACTIONS` then `extra.reactions`; unset
  for amber, beatrice and dizzy, and the effective config has
  `telegram.reactions: false`. No reaction calls in any container log.
- Agent-initiated reactions: `send_message(action="react")` needs
  `add_reaction` on the adapter; the Telegram adapter has none.
- A second process on the same bot: token hashes differ across every
  hermes-agent pod.

## Hermes facts the fix relies on

- `agent/turn_finalizer.py` `apply_llm_output_transform` fires
  `transform_llm_output` once per turn, first string wins, and returns early
  on an empty `final_response`.
- `BasePlatformAdapter._extract_response_content` extracts `MEDIA:` tags from
  the final reply; a reply that is only `[[audio_as_voice]]\nMEDIA:<path>`
  becomes one voice note with no text and no recovery notice. Tagged media is
  not deduplicated against history (only bare paths are).
- Final-reply media and `send_message` use the same
  `validate_media_delivery_path`.
- Plugin `on_session_end` fires at the end of every turn with
  `completed/failed/interrupted` (`agent/turn_finalizer.py`).
- `silence_allowed` permits `NO_REPLY` only on machinery turns, unaddressed
  messages, or a channel with `channel_overrides.<id>.allow_silence: true`.
- An empty reply after tool work becomes a "work done" notice
  (`gateway/run.py` `_normalize_empty_agent_response`).

## Options weighed

1. Media is the reply (chosen by the owner, 2026-11-19): opt-in
   `deliver_media(as_reply=True)`; one message, no ✅. Costs: arrives at turn
   end; `success` means accepted. Turn-end fallback sends directly.
2. `allow_silence` on the private DMs plus `NO_REPLY`: lets the model go silent
   on any healthy turn in those chats; the kit cannot see which chats opted in.
3. Let the model's text through: still two messages, and the model writes
   `NO_REPLY` there, so it would need a filler line.
4. Leave the ✅.

## Not checked

- A live turn with the change (needs the persona-voice/sirens pin bumps and an
  infra promotion).
- sirens' video lane: it records a confirmed-send receipt, so it must keep the
  direct send.
- Whether another plugin's `transform_llm_output` returns a string before the
  kit's on these profiles (the 2026-11-18 log shows memory_sync returning None).
