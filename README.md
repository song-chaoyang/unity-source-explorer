# unity-source-explorer

[![CI](https://github.com/song-chaoyang/unity-source-explorer/actions/workflows/ci.yml/badge.svg)](https://github.com/song-chaoyang/unity-source-explorer/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776ab.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-stdlib%20only-green.svg)](unity-source-explorer/scripts/unityref.py)
[![UnityCsReference](https://img.shields.io/badge/explores-UnityCsReference-238636.svg)](https://github.com/Unity-Technologies/UnityCsReference)
[![Skill package](https://img.shields.io/badge/dist-skill%20package-7c3aed.svg)](https://github.com/song-chaoyang/unity-source-explorer/releases/latest)

**English** | [简体中文](README.zh-CN.md)

> Explore the **real Unity engine C# source** with **zero download**. The only local
> state is the repository address and a ~1 MB file-tree index; source files are
> fetched on demand, scanned in memory, and never stored. Any of 1391 Unity versions
> switches instantly. An optional local clone (one command) unlocks full-speed
> repo-wide search.
>
> Distributed both as a standalone CLI and as an installable **skill package** — the
> `unity-source-explorer.skill` bundle is on the
> [releases](https://github.com/song-chaoyang/unity-source-explorer/releases/latest) page.

Every Unity developer eventually asks: *"what does Unity **actually** do when I call
this?"* Documentation describes the contract; the engine source is the truth — and
Unity publishes the C# layer openly as
[UnityCsReference](https://github.com/Unity-Technologies/UnityCsReference). But it is
a 100 MB repository whose layout changes between versions, core classes live in
`*.bindings.cs` files instead of `TypeName.cs`, and nothing tells you where anything is.

**unity-source-explorer** turns that repository into a queryable knowledge base:

| Need | Command |
|---|---|
| Start using it — nothing to install | `unityref status` (fetches a ~1 MB tree index) |
| Switch Unity version — instantly | `unityref use 2022.3` · `use 6000.6.5f1` · `use latest-stable` |
| Where is a type declared? | `unityref search --def RectTransform` |
| Where is one member declared? | `unityref member Transform position` |
| What is inside a 9,700-line file? | `unityref outline Editor/Mono/EditorGUI.cs` |
| Read an exact region with line numbers | `unityref show <path> --from 26 --to 33` |
| Managed code or native engine code? | outline markers `[extern]` `[native]` `[serialized]` |
| What modules exist? | `unityref tree Modules --depth 1` |
| Power user: full local clone (optional) | `unityref setup` |

## Quick tour

```console
$ unityref status
Mode:     remote (no Unity source stored locally)
Repo:     https://github.com/Unity-Technologies/UnityCsReference.git
Ref:      master
Tree:     8777 files, 8756 C# (~101 MB upstream) - paths+sizes cached at ~/.unitycsref-meta/trees/

$ unityref use 6000.6.5f1
Success: now using tag 6000.6.5f1 (remote mode - no source downloaded, switched instantly)
Tree:     8099 files, 8073 C# sources

$ unityref search --def RectTransform
Runtime/Transform/ScriptBindings/RectTransform.bindings.cs:125:    public sealed class RectTransform : Transform
1 match in 1 file
ref: 6000.6.5f1 (remote mode - files fetched on demand, nothing stored)

$ unityref member Transform position
# Runtime/Transform/ScriptBindings/Transform.bindings.cs (class declared at line 21)
Runtime/Transform/ScriptBindings/Transform.bindings.cs:26:    public extern Vector3 position { get; set; }
1 declaration(s) of 'position' within Transform (remote mode)

$ unityref show Runtime/Transform/ScriptBindings/Transform.bindings.cs --from 26 --to 33
Runtime/Transform/ScriptBindings/Transform.bindings.cs (508 lines) - showing 26..33
   26|         public extern Vector3 position { get; set; }
   28|         // Position of the transform relative to the parent transform.
   29|         public extern Vector3 localPosition { get; set; }
   ...
```

`outline` adds the same 1-based line numbers plus markers — `[extern]` means the body
is native C++ (outside this repository), `[native]` marks binding attributes,
`[serialized]` marks serialization directives.

## How it works

![architecture](docs/architecture.svg)

- **Local state = repo address + file trees only.** One GitHub trees-API call per
  checked-out version caches the full path+size list (~0.6 MB) under `~/.unitycsref-meta`.
- **Files are fetched on demand** from raw.githubusercontent.com, scanned in memory,
  printed, and discarded. Only metadata is ever written to disk.
- **Optional local clone**: `setup` shallow-clones the source (~100 MB). Whenever a
  clone exists (`--root`, `UNITY_CS_REF_PATH`, cwd scan, `~/.unitycsref`) it is
  auto-detected and preferred — ripgrep-speed repo-wide search, offline use, regex
  everywhere. Delete it and you are back in remote mode with all history intact.
- **References**: verified maps of the layout per version era, the native binding
  pipeline, and task recipes teach the tool (and you) *where to look*.

## Install

**As a standalone CLI**

```bash
git clone https://github.com/song-chaoyang/unity-source-explorer.git
cd unity-source-explorer
python3 unity-source-explorer/scripts/unityref.py status
```

Python 3.8+, no third-party dependencies. Optional: `ripgrep` (local clone mode only)
and a `GITHUB_TOKEN` env var (repo-wide remote text search).

**As a skill package**

Download `unity-source-explorer.skill` from the
[releases](https://github.com/song-chaoyang/unity-source-explorer/releases/latest) page
and install it with any CLI that supports the `.skill` format — the SKILL.md inside
teaches the workflow, commands, and reading strategy.

## What's in the box

```
unity-source-explorer/          the skill
├── SKILL.md                   workflow + command reference
├── scripts/unityref.py        the CLI (single file, stdlib only)
└── references/                verified knowledge
    ├── repo-map.md            where everything lives, per version era
    ├── conventions.md         m_/s_/k_ naming, extern & the binding pipeline
    └── recipes.md             task recipes (serialization, coroutines, UGUI, ...)
tests/test_unityref.py          test suite (unit tests run offline, no clone needed)
docs/architecture.svg           the diagram above
```

## Verified facts baked into the references

- Core types are declared in `*.bindings.cs` files — `find -name Transform.cs` finds
  nothing on current versions.
- **UGUI** (Text/Image/Button/Graphic) is *not* in UnityCsReference at all — it lives
  in the separate [uGUI package repo](https://github.com/Unity-Technologies/uGUI).
- 1391 version tags + 34 maintenance branches (`2017.1` … `7000.0` + `master`); the
  repo shape differs per era (2022.3 has ~4.1k .cs files; 7000 master ~8.7k).
- `[WrapperlessIcall]` from older blog posts is gone from current versions.
- 2021.3 / 2022.3 checkouts carry `ModuleOverrides/` — removed in 6000+.

## Notes

- **License** — this project is MIT. The explored source belongs to Unity Technologies
  under the [Unity Reference-Only License](https://unity3d.com/legal/licenses/Unity_Reference_Only_License):
  this tool only *reads* it; do not redistribute Unity's code.
- **Disk** — ~0.6 MB of metadata per checked-out version (remote mode). Optional
  local clone: ~100 MB, plus ~100–300 MB per additionally fetched version.
- **Rate limits** — the trees API allows ~60 unauthenticated calls/h (one per
  version switch); raw file fetches are effectively unlimited. Set `GITHUB_TOKEN`
  for heavy use and repo-wide code search.
- **Slow networks** — `setup --remote <mirror-url>` accepts any git mirror; the
  remote mode itself only needs api.github.com and raw.githubusercontent.com.
