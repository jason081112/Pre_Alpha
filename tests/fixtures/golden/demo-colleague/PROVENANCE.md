# Provenance: demo-colleague

- **Status:** synthetic test fixture. Not a real person, company, or chat export.
- **Source fixture:** `tests/fixtures/chats/colleague_zh.canonical.json`
- **Distilly:** `0afc45c29c360ba7bc8260f1bb0bf34e9155f4fa` (`dot-skill`)
- **Writer:** `vendor/distilly/tools/skill_writer.py`
- **Authoring:** OpenCode agent following the Distilly colleague prompts
- **Provider smoke:** `ollama-cloud/gemma4:31b` (GLM not configured; fallback)
- **Smoke result:** 2 passes × 5 turns, 0 errors, 0 leak markers
- **Generated from synthetic data only:** no keys, raw chats, real names, phone numbers,
  emails, addresses, or absolute paths.

`meta.json` contains the Distilly preset default
`source_context.is_real_person: true`. That is a schema default, not a claim about this
fixture; the authoritative marker is the top-level `"synthetic": true` plus this file.

Do not hand-edit generated artifacts. Regenerate through Distilly, then copy the synthetic
output back into this directory.
