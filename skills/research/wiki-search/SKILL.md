---
name: wiki-search
description: Use when searching a markdown wiki by meaning (not just keywords). Adds semantic vector search to the llm-wiki workflow via local ollama embeddings.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [wiki, search, semantic-search, llm-wiki, markdown]
    related_skills: [llm-wiki, hermes-config-management]
---

# wiki-search — Semantic Vector Search for Markdown Wikis

Add semantic search to any markdown wiki using local ollama embedding models.
Companion to the [llm-wiki](./llm-wiki) skill — enhances `llm-wiki` wikis with
meaning-based retrieval alongside traditional keyword search.

**No database, no external API, no new dependencies.** Needs only ollama (already
installed by most setups) and Python with numpy (standard).

## When to Use

- The user asks a question about their wiki and you need relevant pages by *meaning*, not just exact keywords
- Semantic search would help find related content that doesn't share exact terms
- After writing new wiki content, to re-index before searching
- When the `llm-wiki` keyword search (`search_files`) doesn't find what you need

**Don't use for:** Very small wikis (<5 pages) where browsing the index is faster.
**Don't use when:** ollama is not installed/running — falls back to keyword search.

## Prerequisites

- **ollama** running locally with at least one embedding model. To verify:
  ```bash
  ollama serve &       # start if needed
  ollama pull all-minilm  # lightweight (384d)
  ollama pull nomic-embed-text  # more expressive (768d)
  ```

- **numpy** (Python): `pip install numpy` — usually already installed.

## Quick Start

```bash
# 1. Point to your wiki (default: ~/wiki, or set WIKI_PATH in .env)
export WIKI_PATH=~/my-wiki

# 2. Build the search index
python3 /path/to/wiki-search index

# 3. Search
python3 /path/to/wiki-search "why did xiaos fail with CreditsError"
python3 /path/to/wiki-search --hybrid "photography composition tips"
```

## CLI Reference

```
wiki-search index              Build / update search index
wiki-search <query>            Semantic search (vector only)
wiki-search --hybrid <query>   Hybrid search (vector + keyword RRF)
wiki-search --reindex          Force rebuild all indexes
wiki-search --status           Index stats, ollama health
wiki-search --clean            Delete the index

Options:
  --wiki <path>     Wiki directory (default: $WIKI_PATH or ~/wiki)
  --model <name>    Override embedding model (default: all-minilm)
  --top-k <N>       Results to return (default: 10)
```

## How It Works

1. **Indexing phase:** Walks the wiki directory, skips `raw/`, parses `.md` files into
   sections by `##` / `###` headings. Each section gets an embedding vector via
   ollama's REST API. Vectors are stored in a JSON index at `~/.cache/wiki-search/`.

2. **Search phase:** Embeds the query with the same model, then computes cosine
   similarity against every indexed section. Returns the top-K results sorted by
   relevance.

3. **Hybrid mode (recommended):** Fuses vector similarity scores with keyword
   match counts via Reciprocal Rank Fusion (RRF). Catches both semantic nuance
   and exact term matches.

## Script Location

The script ships as `scripts/wiki-search` inside this skill directory.
To make it globally accessible:

```bash
# Symlink to ~/.local/bin/
ln -sf /path/to/hermes-agent/skills/research/wiki-search/scripts/wiki-search ~/.local/bin/wiki-search
chmod +x ~/.local/bin/wiki-search

# Or alias in .zshrc:
alias wiki-search='python3 /path/to/hermes-agent/skills/research/wiki-search/scripts/wiki-search'
```

## Auto-Indexing (recommended)

Add a cron job or shell hook to keep the index fresh:

```bash
# Re-index daily at 4 AM
0 4 * * * ~/.local/bin/wiki-search --wiki ~/wiki index 2>&1 >/dev/null
```

Or the agent can re-index on demand after writing new wiki content:
```bash
wiki-search --reindex
```

## Search in Agent Conversations

When the conversation touches on topics that might be documented in the wiki,
automatically search before answering:

```python
# In agent workflow
import subprocess
query = user_message  # extract the relevant question
result = subprocess.run(["wiki-search", "--hybrid", query], capture_output=True, text=True)
relevant_pages = json.loads(result.stdout)  # if JSON output is enabled
```

The script currently prints formatted results. Use `--json` (planned) for
programmatic consumption, or parse the terminal output.

## Output Format

```
🔍 "query" — 10 results

  1. [ 62%] ████████████████████
      📄 solutions/xiaos-model-switch.md
      📑 问题
      xiaos profile 报错 `CreditsError: Insufficient balance`...
```

## Common Pitfalls

1. **Index is stale.** After adding new pages or editing existing ones, run
   `wiki-search --reindex` to pick up changes. The incremental indexer detects
   mtime changes, but always re-index after a batch of edits.

2. **Ollama not running.** If you see "cannot reach ollama", start it:
   `ollama serve`. The error message tells you this.

3. **Model mismatch.** If you switch embedding models (e.g., from all-minilm to
   nomic-embed-text), clear the index first: `wiki-search --clean && wiki-search index`.
   Mixing embeddings from different models produces garbage similarity scores.

4. **Very large wikis (5000+ files).** The JSON index loads entirely into memory.
   For large wikis, consider splitting the index per-section or migrating to a
   lighter vector DB. For typical personal wikis (under 500 files), JSON is fine.

5. **Runs as a script, not installed binary.** The skill ships the Python source
   in `scripts/`. Users need to symlink or alias it for convenient use.

## Verification Checklist

- [ ] `ollama serve` is running and `ollama pull all-minilm` completed
- [ ] `wiki-search --status` shows "Ollama: ✅ running"
- [ ] `wiki-search index` completes without errors
- [ ] `wiki-search "test query"` returns results with relevance bars
- [ ] `wiki-search --hybrid "test query"` runs without errors
- [ ] After editing a wiki page, `wiki-search --reindex` picks up the change
- [ ] `wiki-search --clean` clears the index and rebuild succeeds

## Related

- [llm-wiki](./llm-wiki) — The markdown wiki approach this skill enhances
- [ollama](https://ollama.com) — Local LLM runtime with embedding support
- [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — Original concept
