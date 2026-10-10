# UnityCsReference repo map

Verified against the actual repository (master = Unity 7000.0.0a8, checked 2026-10). The
upstream README warns: *"The folder and file layout of the reference source matches the
Unity source tree layout; it can and will change between different Unity versions."*
Always re-verify paths with `unityref search` / `tree` on the checked-out version.

## Version landscape

- 1391 version tags + **34 maintenance branches** (`2017.1` ... `7000.0` + `master`,
  one per release line). `use <line>` (e.g. `2022.3`) checks out the moving branch tip;
  `use <exact tag>` (e.g. `2022.3.76f1`) pins a release. Tag suffixes: `a` = alpha,
  `b` = beta, `f` = final/stable.
- Newest stable (Oct 2026): `6000.6.5f1`. Active LTS lines: `2022.3`, `2021.3`.
  `7000.0` is an alpha; master tracks the newest.
- Layout eras (relevant when checking out older lines):
  - **2021.3 / 2022.3**: root has `ModuleOverrides/` (gone in 6000+); repo is much
    smaller (~4.1k .cs files vs ~8.7k on master).
  - **6000.x**: same shape as master.
- **UGUI (Canvas-based UI: Text/Image/Button/Graphic) is NOT in this repo** — in any
  version line. It lives in the separate package repo
  https://github.com/Unity-Technologies/uGUI (default branch `main`).
  `Modules/UI` here contains only the Canvas-family engine bindings
  (`UICanvas.bindings.cs`, `CanvasRenderer`, `CanvasGroup`, `RectTransformUtility`);
  `Modules/UI/Managed` holds at most a help-URL helper (6000.x) or nothing (2022.3).

## Root layout (master)

| Dir | What it is | Scale |
|---|---|---|
| `Runtime/` | Core engine C#: `Export/` is the heart of the UnityEngine surface | 532 .cs |
| `Editor/` | UnityEditor C#, almost everything under `Mono/` | 1529 .cs |
| `Modules/` | Feature modules, `<Name>/{Managed, ScriptBindings}` per module | 6599 .cs |
| `Projects/CSharp/` | `UnityReferenceSource.sln` + UnityEngine/UnityEditor .csproj — open this to browse in an IDE | — |
| `artifacts/` | Generated odds and ends (e.g. `generated/UnityEvent/UnityEvent_0..4.cs`) | 6 .cs |
| `External/` | Vendored third-party: CoreCLR, Unity.Cecil, ScriptingCore, baselib, il2cpp build configs | 89 .cs |
| `Tools/` | Unity's internal build system (Bee) — not engine code | 1 .cs |
| `ModuleOverrides/` | Only in 2021.3 / 2022.3 | — |

Total on master: ~8.7k C# files, ~870 `*.bindings.cs`, ~100 MB working tree.

## Key locations (verified on master)

| Thing | Where |
|---|---|
| `UnityEngine.Object` | `Runtime/Export/Scripting/UnityEngineObject.bindings.cs` |
| `GameObject` / `MonoBehaviour` / `Component` / `Behaviour` / `ScriptableObject` / `Coroutine` / `AsyncOperation` / `GUID` / `LayerMask` / `Awaitable` | `Runtime/Export/Scripting/*.bindings.cs` |
| `Transform` / `RectTransform` | `Runtime/Transform/ScriptBindings/` |
| `PlayerLoop` (struct) | `Runtime/Export/PlayerLoop/PlayerLoop.bindings.cs` |
| Serialization attributes (`SerializeField`, ...) | `Modules/Scripting/Attributes/` (`SerializeFieldAttribute.cs`) |
| Binding infrastructure (`[NativeHeader]`, `[NativeMethod]`, ...) | `Modules/Scripting/Attributes/BindingsAttributes.cs` |
| `Canvas` / `CanvasRenderer` / `CanvasGroup` / `RectTransformUtility` | `Modules/UI/ScriptBindings/` |
| UGUI runtime (`Graphic`, `Text`, `Image`, `Button`) | NOT in this repo — separate https://github.com/Unity-Technologies/uGUI |
| UI Toolkit (`VisualElement`, `Button`, `Toggle`, ...) | `Modules/UIElements/Core/` |
| Physics (`Rigidbody`, `Collider`, ...) | `Modules/Physics/{Managed, ScriptBindings}` |
| Input System | `Modules/Input` (legacy: `Modules/InputLegacy`; UI-facing: `Modules/InputForUI`) |
| `Event` (input events) | `Modules/UICommon/Event.cs` |
| `SerializedObject` / `SerializedProperty` (editor) | `Editor/Mono/SerializedObject.bindings.cs` |
| `EditorGUI` / `EditorGUILayout` / `EditorGUIUtility` | `Editor/Mono/` |
| AssetDatabase pipeline | `Editor/Mono/AssetDatabase/`, `Modules/AssetPipelineEditor` |
| Mathematics (`float2`, `int3`, ...) | `Modules/Mathematics` |
| Jobs | `Runtime/Jobs/` + per-module `Jobs` dirs |
| Profiler markers / APIs | `Runtime/Profiler/`, `Modules/ProfilerEditor` |
| Runtime serialization internals | `Runtime/Serialize/Managed` |

`Runtime/Export/` is split into ~77 domain subdirs (`Application`, `Camera`, `Graphics`,
`Debug`, `Audio`, `Input`, `Gizmos`, ...) — each exporting that slice of UnityEngine.

## What this repo does NOT contain

- The native C++ engine implementation. `extern` members and `[NativeHeader("...")]`
  pointers reference C++ files that are **not** in this repo.
- The binding generator's *output* (generated marshaling wrappers, produced when Unity
  builds the engine).
- The `com.unity.ugui` package source (UGUI) — all versions; see the separate uGUI repo.
- Burst compiler, editor native code, shader compilers.

## Practical notes

- A "definition file" for a core class is usually `TypeName.bindings.cs`, **not**
  `TypeName.cs` — `find -name Transform.cs` finds nothing on master.
- Module internal layout varies: big modules have `Managed/` (pure C#) +
  `ScriptBindings/` (native-backed); small ones are just a few `.bindings.cs` files.
- Editor bindings are colocated in `Editor/Mono` next to handwritten editor code.
- The upstream license is **Unity Reference-Only License**: read, don't redistribute.
  Don't paste large chunks of this source into user projects.
