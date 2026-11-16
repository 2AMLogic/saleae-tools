# Work Log

Chronological record of merged PRs and closed issues. Maintained by the Guide role.

### 2026-11-16

- **PR #52**: refactor: drop i2c's _level copy of DigitalChunk.level_at
- **PR #50**: docs: replace line-range sync check with marker-anchored diff
- **Issue #51** (closed): Remove i2c.py's _level helper: the third copy of DigitalChunk.level_at that #32 hoisted out of uart.py and spi.py
- **Issue #48** (closed): Make the AGENTS.md/CLAUDE.md sync proof range-free so appended drift cannot escape it

### 2026-11-15

- **PR #46**: refactor: share decoder command scaffolding in cli.py
- **Issue #45** (closed): Deduplicate the four decoder commands' scaffolding in cli.py (channel paths, --expect parsing/printing, JSON emit)
- **PR #47**: feat(uart): report measured baud and drift, with --baud-tolerance
- **PR #44**: feat: add slt i2c, an offline decoder for I2C transactions
- **PR #43**: docs: make AGENTS.md and CLAUDE.md note regions byte-identical
- **PR #41**: fix: defer unset capture-board wrapper flags to the profile
- **PR #40**: refactor: give the shared --expect matcher a typed home in expect.py
- **PR #36**: refactor: replace the duplicated _level helper with DigitalChunk.level_at
- **PR #26**: docs: roadmap as of 2026-11-13
- **Issue #39** (closed): Remove the duplicated capture defaults in scripts/capture-board.py: a third copy that silently overrides the profile's [trigger] table
- **Issue #37** (closed): Stop maintaining two wordings of the project notes: AGENTS.md and CLAUDE.md already drifted once
- **Issue #33** (closed): Give the shared --expect matcher a home: move match_expected out of uart.py and type its result
- **Issue #32** (closed): Remove the duplicated `_level` helper: hoist it onto `DigitalChunk` before a third copy lands
- **Issue #30** (closed): Report measured bit time and sampling drift in `slt uart`, with an optional tolerance
- **Issue #27** (closed): Add an offline I2C decoder and `slt i2c --expect` over digital exports (bring-up stage 5)
- **PR #35**: feat: bound pulse widths and periods in slt timing so it can fail a run
- **Issue #29** (closed): Let `slt timing` fail a run: min/max bounds on pulse widths and periods

### 2026-11-14

- **PR #38**: feat: add slt syncserial, an offline decoder for the loader's clocked serial
- **Issue #28** (closed): Add an offline clocked-serial decoder and `slt` verb for the program loader (bring-up stage 2)

### 2026-11-13

- **PR #24**: docs: correct AGENTS.md MCP bullet to .mcp.json.bench
- **Issue #23** (closed): AGENTS.md:22 names a .mcp.json that does not exist (file is .mcp.json.bench)
- **PR #22**: docs: drop committed MCP schema snapshot, regenerate with slt mcp-tools
- **Issue #19** (closed): Remove docs/mcp-tools.json: 9.7k-line regenerable snapshot nothing reads

### 2026-11-12

- **PR #17**: Bump Repo Skills 0.25.10 -> 0.26.1

### 2026-11-09

- **PR #15**: Bump ruff from 0.17.1 to 0.17.2 in the minor-patch group

### 2026-11-07

- **PR #13**: feat: add offline SPI decoder and slt spi verb
- **Issue #12** (closed): Add an offline SPI decoder and `slt spi --expect` over digital exports (bring-up stage 4)
- **PR #10**: Bump Repo Skills 0.12.2 -> 0.25.10

### 2026-11-06

- **PR #8**: feat: add offline UART decoder and slt uart verb
- **Issue #7** (closed): Add an offline 8N1 UART decoder and `slt uart --expect` byte assertion over digital exports

### 2026-11-05

- **PR #5**: Bump the minor-patch group with 2 updates
- **PR #4**: Bump the github-actions group with 2 updates
- **PR #3**: ci: add Dependabot config for uv and GitHub Actions
- **Issue #2** (closed): Add .github/dependabot.yml for uv and GitHub Actions per the rjwalters org policy

### 2026-11-04

- **PR #1**: Install Loom ahead of fleet admission
