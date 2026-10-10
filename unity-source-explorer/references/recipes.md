# Exploration recipes

Task-oriented recipes. Every command assumes `unityref` =
`python3 <skill-dir>/scripts/unityref.py`. Adapt names to the user's question.

No local clone is required — remote mode fetches files on demand and stores nothing.
Repo-wide scans (`--text` / `--symbol` / `--regex` WITHOUT `--path`) additionally need
a `GITHUB_TOKEN` env var or a local clone (`setup`); everything else works everywhere.

## 1. "Where is X implemented?" (any Unity type/member)

```bash
unityref search --def RectTransform          # definition file(s)
unityref outline RectTransform               # structure with line numbers
```
Then read the exact region: `unityref show <path> --from N --to M` (remote mode
fetches on demand; in local mode the agent's native file tools also work directly).
Core types live in `*.bindings.cs` — see repo-map.md before guessing paths.

## 2. "What does property/method Y actually do?"

```bash
unityref member Transform position           # exact declaration + line
unityref outline Transform --filter '^[gs]et_'   # narrow to accessors
```
- No `[extern]` marker → the C# body on those lines IS the behavior. Quote it
  (`show <path> --from <line>`).
- `[extern]` → native side not in repo; report the signature, doc comment, and
  `[NativeHeader]` instead (see conventions.md). Don't invent native behavior.

## 3. "Find every native entry point of a type"

```bash
unityref outline Transform --max 300          # scan for [extern] / [native] markers
unityref search --text 'extern' --path 'Runtime/Transform/**'
```

## 4. "How did this change between Unity versions?"

```bash
unityref use 2022.3          # version-line branch tip (moves with new patches)
unityref use 2022.3.76f1     # exact release pin
unityref status
unityref use master          # back to newest
```
Switching is instant in remote mode (only the ref + file tree update). With a local
clone, each newly fetched version downloads ~100-300 MB. For actual diffs use
https://github.com/Unity-Technologies/UnityCsReference/compare/<tagA>...<tagB>
(the remote-mode checkouts are metadata-only), or outline the same symbol in two
versions and compare line by line.

## 5. UGUI vs UI Toolkit

- UGUI (`Graphic`, `Text`, `Image`, `Button`): **not in this repo at all** — it is the
  com.unity.ugui package, source at https://github.com/Unity-Technologies/uGUI
  (branch `main`). For UGUI internals, browse that repo instead; UnityCsReference
  holds only the Canvas-family engine bindings.
- Canvas / CanvasRenderer / CanvasGroup / RectTransformUtility (all versions):
  `Modules/UI/ScriptBindings/`.
- UI Toolkit (`VisualElement`, `Button`, `Toggle`): `Modules/UIElements/Core/`.
- Disambiguation trick: `unityref outline Button` lists every candidate file.

## 6. Serialization questions

- `[SerializeField]` directive itself:
  `Modules/Scripting/Attributes/SerializeFieldAttribute.cs`
- Editor pipeline: `unityref outline SerializedObject` →
  `Editor/Mono/SerializedObject.bindings.cs`; `SerializedProperty` and
  `Editor/Mono/AssetDatabase/` next door.
- Runtime side: `Runtime/Serialize/Managed`, `Modules/JSONSerialize`.
- "Why isn't my field saved" → check `m_` naming + `[FormerlySerializedAs]` usages:
  `unityref search --text 'FormerlySerializedAs' --path 'Modules/**'`
  (that scope is ~6.6k files — a local clone or GITHUB_TOKEN is recommended).

## 7. Coroutines / async / player loop

- Coroutine: `Runtime/Export/Scripting/Coroutine.bindings.cs` — tiny; the scheduler is
  native. `CustomYieldInstruction.cs` in the same dir is managed.
- Modern async: `Runtime/Export/Scripting/Awaitable*.cs` (async method builder,
  awaiter, delayed call, threading) — the currently recommended pattern.
- PlayerLoop: `Runtime/Export/PlayerLoop/PlayerLoop.bindings.cs` — struct + subsystem
  lists; the insertion points user code hooks into.

## 8. Editor UI internals

- IMGUI: `Editor/Mono/EditorGUI.cs` (~9.7k lines) — use `--filter` heavily, e.g.
  `unityref outline Editor/Mono/EditorGUI.cs --filter '^Draw.*Field$'`.
- `EditorGUILayout`, `EditorGUIUtility`, `GUIStyle`: `Editor/Mono/` siblings.
- UI Toolkit editor side: `Modules/UIElementsEditor`; styling: `Modules/StyleSheetsEditor`.

## 9. Input & events

- `Event` (keyboard/mouse event struct): `Modules/UICommon/Event.cs`.
- New Input System: `Modules/Input`; legacy: `Modules/InputLegacy`;
  UI-facing input: `Modules/InputForUI`.

## 10. "Is this API editor-only / thread-safe / stripped?"

- Editor-only: lives under `Editor/`, or `[NativeConditional("UNITY_EDITOR")]`, or
  `#if UNITY_EDITOR` region — `unityref search --text 'NativeConditional' --path 'Modules/Scripting/**'`.
- Thread-safe bindings: `[NativeMethod(IsThreadSafe = true)]` —
  `unityref search --regex 'NativeMethod\(IsThreadSafe'` (clone/token needed for
  repo-wide, or scope it with `--path`).
- Stripping: `[RequiredByNativeCode]` / `[Preserve]` markers.

## Cross-checking against the user's Unity version

`unityref status` shows the checked-out ref — always MATCH the user's project
version first (`unityref use 6000.6.5f1` etc.), because signatures and even file
locations differ across versions. The repo grows with each line (2022.3: ~4.1k .cs
files; 7000 master: ~8.7k). Same-version answers require same-version checkouts.
