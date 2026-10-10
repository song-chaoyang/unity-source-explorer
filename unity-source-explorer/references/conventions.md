# Code conventions & the native binding pipeline

Verified on master (7000.0.0a8). This is the knowledge you need to read Unity's C#
fluently and to know when you have hit the native wall.

## Naming conventions

- `m_Name` — private instance fields (`private int m_CullingMode;`)
- `s_Name` — private static fields
- `k_Name` / `kName` — constants and readonly statics; both spellings exist
  (`k_MultiEditValueString`, `kFloatFieldFormatString`)
- PascalCase everywhere in public API; `internal` is used freely for engine plumbing
- Attribute classes may drop the `Attribute` suffix: `[SerializeField]` is declared as
  `public sealed partial class SerializeField : Attribute` in
  `Modules/Scripting/Attributes/SerializeFieldAttribute.cs`
- Doc comments (`// Summary text.` or `///`) precede almost every public member —
  reading them is the fastest way to learn intended behavior

## File naming

- `TypeName.bindings.cs` — the definition file for native-backed types (the API surface)
- `TypeName.cs` — pure managed implementation
- `TypeName.deprecated.cs` — deprecated API kept for backward compatibility
- Definition lives in a `.bindings.cs` while heavy logic often sits in `Managed/` of the
  same module, split via `partial class`

## The binding pipeline (the most important thing to understand)

Almost every property/method you care about looks like this:

```csharp
[NativeHeader("Runtime/Transform/Transform.h")]           // native C++ file implementing it
[RequiredByNativeCode]                                     // survive managed stripping
public partial class Transform : Component, IEnumerable
{
    // The position of the transform in world space.
    public extern Vector3 position { get; set; }           // body lives in C++!
}
```

- `extern` members have **no C# body** — the body is native C++, not in this repo.
  The binding generator creates the marshaling wrapper at engine build time; that
  generated code is *not* here either. So you cannot trace past `extern` in this repo —
  but you can learn *what* is called and *what the C# side does around it*.
- A member with a real C# body (e.g. `Transform.eulerAngles` calling
  `Quaternion.Euler`) is fully readable — behavior is right there.
- Attribute usage scale on master: `[NativeHeader]` 912 files, `[RequiredByNativeCode]`
  540, `[NativeMethod]` 381, `[FreeFunction]` 377, `[NativeClass]` 121,
  `[UnmanagedFunctionPointer]` 10.

### Binding attributes you will meet

| Attribute | Meaning |
|---|---|
| `[NativeHeader("path.h")]` | native header implementing the type |
| `[NativeMethod(...)]` | native method binding options (thread safety, name override) |
| `[FreeFunction]` | free C function, not a class method |
| `[NativeConditional("UNITY_EDITOR")]` | binding only exists in editor builds |
| `[RequiredByNativeCode]` / `[UsedByNativeCode]` | must survive managed stripping |
| `[NativeClass("Transform", PersistentTypeId = 4)]` | native class identity |
| `[SerializeField]` / `[FormerlySerializedAs("m_OldName")]` | serialization directives |

Definitions of all binding attributes: `Modules/Scripting/Attributes/BindingsAttributes.cs`.

## Structure conventions

- `partial` classes everywhere — expect a type to be split across several files; use
  `unityref search --def TypeName` to find them all.
- Block-style namespaces (`namespace UnityEngine {`), 4-space indent.
- Every file starts with `// Unity C# reference source` + license pointer.
- New internal pattern on master: `internal Transform(global::UnityEngine.EntityId id)`
  constructors — native object identity handle. Treat as plumbing.
- Editor code is guarded by `#if UNITY_EDITOR` in shared files, or lives under `Editor/`.
- There are **no asmdef files** on master; IDE browsing uses
  `Projects/CSharp/UnityReferenceSource.sln`.
- Historical note: old articles mention `[WrapperlessIcall]` — it is **gone** from
  current versions (0 hits on master). Don't chase it.

## Reading strategy

1. `unityref outline <Type>` — see the member list with `[extern]`/`[native]` markers.
2. Members without markers → read the C# body in place (full behavior visible).
3. Members with `[extern]` → behavior is native; the C# side still shows signature,
   docs, and any managed wrapper logic around it.
4. `[NativeHeader]` tells you which C++ file *would* contain it (outside this repo) —
   useful for bug reports and for reasoning about build-target differences.
