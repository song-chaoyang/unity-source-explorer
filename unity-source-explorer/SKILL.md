---
name: unity-source-explorer
description: Explore Unity engine C# source code via the UnityCsReference repository with zero download — only the repo address and a ~1 MB file-tree index are stored locally; source files are fetched on demand and never saved (an optional local clone is auto-detected for full-speed offline search). Locate where UnityEngine/UnityEditor APIs are defined, read their real implementation, trace native binding boundaries, and switch instantly between 1391 Unity version tags and 34 maintenance branches. Use when the user asks how Unity works internally, where an API (Object.Destroy, RectTransform, SerializeField, coroutines, PlayerLoop, EditorGUI...) is implemented, or when debugging behavior that requires reading engine source. Bundles the unityref CLI: definition/symbol search, member location, line-numbered outlines with extern/native markers, and on-demand line-range file reading.
---

# Unity Source Explorer

Explore the real Unity engine C# source (Unity-Technologies/UnityCsReference) instead
of guessing from documentation — **without downloading it**. Local state is only the
repo address and a ~1 MB file-tree index; files are fetched on demand, scanned in
memory, and never written to disk.

## Modes

- **remote (default)** — zero setup, zero download. Metadata in `~/.unitycsref-meta`
  (config + one tree JSON per checked-out version). Version switching is instant.
- **local clone (auto-detected accelerator)** — if a UnityCsReference clone exists
  (`--root` flag, `$UNITY_CS_REF_PATH`, cwd or a parent dir, `~/.unitycsref`), it is
  used automatically: ripgrep-speed repo-wide search, offline, regex everywhere.
  Create one optionally with `setup`.

## Core workflow

1. **Locate**: `search --def <Type>` → definition file(s) + line
2. **Understand structure**: `outline <Type>` → members with 1-based line numbers
3. **Read**: `show <path> --from N --to M` → exactly that region (remote mode fetches
   on demand; in local mode native file tools also work directly on the repo root)
4. **Pinpoint**: `member <Type> <name>` → declaration lines of one member

Outline markers: `[extern]` = body in native C++ (not in this repo), `[native`] =
native binding attributes, `[serialized]` = serialization directives. Read
[references/conventions.md](references/conventions.md) to interpret them properly.

## Command reference

All commands: `python3 <skill-dir>/scripts/unityref.py <command>` (global `--root`
goes before the subcommand).

| Command | What it does |
|---|---|
| `status` / `where` | mode, repo address, current ref, tree stats |
| `use <ref>` | tag (`6000.6.5f1`), version line (`2022.3` = moving branch tip), `latest`, `latest-stable`, `master` — instant in remote mode |
| `versions [--stream 2022.3]` | upstream tags + streams |
| `update` | refresh the current ref (tree in remote mode, checkout in local mode) |
| `search --def X` | class/struct/interface/enum/record/delegate declarations of X |
| `search --symbol x` | member declarations (methods/properties/fields) named x |
| `search --text x` / `--regex x` | substring / raw regex search |
| `search ... --path 'Modules/UI/**'` | glob filter (no slash = basename match) |
| `member Type name` | declarations of `name` inside Type's file(s) |
| `outline <Type or path>` | structural outline with line numbers; `--filter`, `--types-only`, `--max` |
| `show <path> --from N --to M` | line-numbered file region, fetched on demand |
| `tree [subpath] --depth N` | directory tree with .cs / bindings counts (from metadata in remote mode) |
| `setup` | OPTIONAL: shallow-clone the full source (~100 MB) for local mode |

`-i` = case-insensitive. `--max` caps output (search 100, outline 300, tree 400).

## Remote-mode constraints (important)

- `--def` and `member` work everywhere via filename conventions
  (`TypeName.cs`, `TypeName.bindings.cs`, `UnityEngineTypeName.bindings.cs`).
  Outlier: `UnityEngine.Object` lives in `UnityEngineObject.bindings.cs`.
- Repo-wide `--text` / `--symbol` / `--regex` scans need either `--path 'glob/**'`
  scoping (`--max-files`, default 60, caps fetched files), or a `GITHUB_TOKEN` env var
  (GitHub code search), or a local clone.
- GitHub API limits: ~60 requests/h unauthenticated — one tree call per version is
  cheap; raw file fetches don't count against it.

## Version awareness

**Match the user's Unity version before drawing conclusions** — paths, signatures and
whole modules differ across versions. `status` shows the current ref; `use` switches
it instantly (remote mode). Known big difference: UGUI source (Text/Image/Button/
Graphic) is NOT in this repo in any version — it lives in the separate
https://github.com/Unity-Technologies/uGUI package repo.

## When to read which reference

- [references/repo-map.md](references/repo-map.md) — BEFORE guessing paths: verified
  directory layout per era, key type → file table, what is not in the repo.
- [references/conventions.md](references/conventions.md) — naming (`m_`/`s_`/`k_`),
  the bindings pipeline (`extern`, `[NativeHeader]`, `partial`), telling managed vs
  native code apart.
- [references/recipes.md](references/recipes.md) — task recipes: version comparison,
  serialization, coroutines/Awaitable, editor UI, UGUI vs UI Toolkit, thread-safety,
  stripping.

## Limits & license

- Upstream license is **Unity Reference-Only**: read, don't redistribute. Quote short
  snippets; never copy large chunks into user projects.
- Native C++ engine code is not here — `extern` members stop at the boundary;
  `[NativeHeader]` names the C++ file (outside the repo).
- Branch refs move upstream; refresh with `update`. Tags are immutable.
