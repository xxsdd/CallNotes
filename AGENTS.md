# CallNotes maintenance

- Keep the user's text-first workflow: recording/import transcribes without speaker diarization. Only an explicit user action with a chosen participant count runs speaker models.
- Every completed application update must add a user-facing entry to `CHANGELOG.md`. Bump `callnotes/release.py` `VERSION` for each subsequent released update, and keep its newest changelog heading in sync. Do not assign retroactive version numbers or dates without evidence.
- Log only implemented behavior. Unfinished plans, model downloads, or tests alone are not released features. Keep transcript revision numbers distinct from the application version.
- UI changes must support both global day and night themes. Check the history context menu, fullscreen reader, citations, and changelog as applicable.
- Preserve recordings, old transcript versions, citation provenance, and active tasks. Verify the runtime is idle before restarting; do not force-kill it to deploy an update.
- Codex analysis must use confirmed ephemeral sessions, never persistent thread/start or thread/resume. Recover follow-up context only from the same record and transcript revision's local messages; do not delete or archive the user's pre-existing Codex chats.
