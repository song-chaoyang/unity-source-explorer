#!/usr/bin/env python3
"""Tests for unityref.py.

Run:  python3 tests/test_unityref.py
No clone is required (pure-function tests). If a UnityCsReference clone exists
at the default location, a few integration checks run too.

Exit code 0 = all pass, 1 = at least one failure.
"""

import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "unity-source-explorer", "scripts", "unityref.py")

spec = importlib.util.spec_from_file_location("unityref", SCRIPT)
ur = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ur)

failures = []
passed = 0


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("PASS  %s" % name)
    else:
        failures.append(name)
        print("FAIL  %s   %s" % (name, detail))


# ---------------------------------------------------------------------------
# version key ordering
# ---------------------------------------------------------------------------

check("vkey: newer minor line wins", ur.vkey("6000.7.0b4") > ur.vkey("6000.6.5f1"))
check("vkey: f beats b on same base", ur.vkey("6000.7.0f1") > ur.vkey("6000.7.0b9"))
check("vkey: a < b < f", ur.vkey("7000.0.0a1") < ur.vkey("7000.0.0b1") < ur.vkey("7000.0.0f1"))
check("vkey: numeric not lexicographic", ur.vkey("2022.3.76f1") > ur.vkey("2022.3.9f1"))
check("vkey: tag parse fallback", ur.vkey("2022.3") == (0, 0, 0, -1, 0))

# ---------------------------------------------------------------------------
# glob translation
# ---------------------------------------------------------------------------

p = re.compile(ur.glob_to_regex("Modules/UI/**"))
check("glob: ** crosses dirs", p.fullmatch("Modules/UI/ScriptBindings/UICanvas.bindings.cs"))
check("glob: anchored prefix", p.fullmatch("Modules/UIElements/Core/Button.cs") is None)
p = re.compile(ur.glob_to_regex("*.bindings.cs") + "$")
check("glob: basename pattern", p.match("Transform.bindings.cs"))
check("glob: basename rejects others", p.match("Transform.cs") is None)

# ---------------------------------------------------------------------------
# def / symbol patterns
# ---------------------------------------------------------------------------

dp = ur.def_pattern("Transform")
check("def: matches declaration", re.search(dp, "public partial class Transform : Component"))
check("def: matches struct", re.search(dp, "internal struct Transform"))
check("def: word boundary", re.search(dp, "class TransformStream") is None)
check("def: multiline-base decl still hits",
      re.search(ur.def_pattern("Graphic"), "public abstract class Graphic"))
sp = ur.symbol_pattern("StartCoroutine")
check("symbol: method decl matches", re.search(sp, "public Coroutine StartCoroutine(IEnumerator routine)"))
check("symbol: mid-statement call not matched",
      re.search(sp, "    var ok = StartCoroutine(routine);") is None)
# bare line-start calls do match the raw regex by design; line_is_decl rejects them
check("symbol: bare call rejected by decl filter",
      not ur.line_is_decl("StartCoroutine(routine);",
                          re.compile(re.escape("StartCoroutine")), "StartCoroutine"))

name_re = re.compile(re.escape("StartCoroutine"))
check("line_is_decl: accepts declaration",
      ur.line_is_decl("public Coroutine StartCoroutine(IEnumerator routine) {", name_re, "StartCoroutine"))
check("line_is_decl: rejects statement",
      not ur.line_is_decl("StartCoroutine(routine);", name_re, "StartCoroutine"))
check("line_is_decl: rejects return",
      not ur.line_is_decl("return StartCoroutine(foo);", name_re, "StartCoroutine"))
check("line_is_decl: accepts field",
      ur.line_is_decl("private int m_Count;", re.compile(re.escape("m_Count")), "m_Count"))
check("line_is_decl: rejects type decl line",
      not ur.line_is_decl("class Foo : Bar", re.compile(re.escape("Foo")), "Foo"))

# ---------------------------------------------------------------------------
# comment/string stripping
# ---------------------------------------------------------------------------

src = (
    "// line comment\n"
    "/* block\n"
    "   comment */\n"
    "string s = \"a{b}c\";\n"
    "string v = @\"x\"\"y{\";\n"
    "char c = '{';\n"
    "int x = 1; // trailing comment\n"
)
out = ur.strip_comments_strings(src)
check("strip: comments removed", "comment" not in out)
check("strip: string content removed", "a{b}c" not in out)
check("strip: braces in strings removed", "{" not in out)
check("strip: code preserved", "int x = 1;" in out)
check("strip: newline count preserved", out.count("\n") == src.count("\n"))

# ---------------------------------------------------------------------------
# outline parsing
# ---------------------------------------------------------------------------

SAMPLE = """// Unity C# reference source
namespace UnityEngine
{
    public enum RotationOrder { OrderXYZ, OrderZYX }

    public enum Multi
    {
        A,
        B = 5,
    }

    [NativeHeader("Runtime/X.h")]
    public partial class Fake : Component
    {
        [SerializeField]
        private int m_Count;

        [NativeMethod(IsThreadSafe = true)]
        internal extern void DoNative(int x);

        public int Count { get { return m_Count; } set { m_Count = value; } }

        public event System.Action OnDone;

        internal Fake() { }

        public void Managed(int y) { DoNative(y); }
    }
}
"""

entries, counts = ur.parse_outline(SAMPLE)
names = [e[3] for e in entries]
kinds = {}
for e in entries:
    kinds.setdefault(e[2], []).append(e)

check("outline: namespace found", "UnityEngine" in names)
check("outline: class found", "Fake" in names)
check("outline: ctor found", "Fake" in [e[3] for e in kinds.get("method", [])])
check("outline: extern method found", "DoNative" in names)
check("outline: property found", "Count" in names)
check("outline: field found", "m_Count" in names)
check("outline: event found", "OnDone" in names)
check("outline: plain method found", "Managed" in names)
check("outline: multi-line enum members", counts.get("enum-member", 0) >= 2,
      "counts=%s" % counts)
check("outline: enum tracked as enum", counts.get("enum", 0) == 2, "counts=%s" % counts)

donative = [e for e in entries if e[3] == "DoNative"][0]
check("outline: extern marker", "extern" in donative[5], donative[5])
check("outline: native marker", "native" in donative[5], donative[5])
mcount = [e for e in entries if e[3] == "m_Count"][0]
check("outline: serialized marker", "serialized" in mcount[5], mcount[5])
fake = [e for e in entries if e[3] == "Fake"][0]
check("outline: class carries native attr marker", "native" in fake[5], fake[5])
line_of = {e[3]: e[0] for e in entries}
check("outline: line numbers increase", line_of["UnityEngine"] < line_of["Fake"] < line_of["Managed"])

# ---------------------------------------------------------------------------
# remote-mode helpers (no network needed)
# ---------------------------------------------------------------------------

FAKE_TREE = [
    ["Runtime/Export/Scripting/UnityEngineObject.bindings.cs", 100],
    ["Runtime/Export/Scripting/GameObject.bindings.cs", 100],
    ["Runtime/Export/Scripting/Awaitable.AsyncMethodBuilder.cs", 30],
    ["Runtime/Transform/ScriptBindings/Transform.bindings.cs", 100],
    ["Modules/UI/ScriptBindings/UICanvas.bindings.cs", 50],
    ["Modules/UIElements/Core/Controls/Button.cs", 40],
]

check("candidates: exact stem ranked first",
      ur.type_candidates(FAKE_TREE, "Transform")[0].endswith("Transform.bindings.cs"))
check("candidates: plain .cs exact match",
      any(c.endswith("Controls/Button.cs") for c in ur.type_candidates(FAKE_TREE, "Button")))
check("candidates: suffix heuristic (Object -> UnityEngineObject)",
      any("UnityEngineObject.bindings.cs" in c for c in ur.type_candidates(FAKE_TREE, "Object")))
check("candidates: prefix heuristic (Canvas -> UICanvas)",
      any("UICanvas.bindings.cs" in c for c in ur.type_candidates(FAKE_TREE, "Canvas")))
check("candidates: cap respected", len(ur.type_candidates(FAKE_TREE, "e", 2)) <= 2)

check("sanitize_ref: keeps version tags", ur.sanitize_ref("6000.6.5f1") == "6000.6.5f1")
check("sanitize_ref: escapes separators", ur.sanitize_ref("a/b c") == "a_b_c")

check("path_matches: anchored glob", ur.path_matches("Modules/UI/Core/x.cs", "x.cs", "Modules/UI/**"))
check("path_matches: rejects sibling dirs",
      not ur.path_matches("Modules/UIElements/Core/x.cs", "x.cs", "Modules/UI/**"))
check("path_matches: basename miss", not ur.path_matches("a/b/c.cs", "c.cs", "*.bindings.cs"))
check("path_matches: basename hit", ur.path_matches("a/b/c.bindings.cs", "c.bindings.cs", "*.bindings.cs"))
check("filter_paths: counts", len(ur.filter_paths(FAKE_TREE, "Runtime/**")) == 4)

check("era_from_tree: legacy detection", "ModuleOverrides" in ur.era_from_tree([["ModuleOverrides/x", 1]]))
check("era_from_tree: modern detection", "modern" in ur.era_from_tree(FAKE_TREE))

# ---------------------------------------------------------------------------
# integration (optional, requires a clone at the default location)
# ---------------------------------------------------------------------------

if ur.is_unitycs_repo(ur.default_root()):
    root = ur.default_root()

    r = subprocess.run([sys.executable, SCRIPT, "search", "--def", "Coroutine", "--max", "5"],
                       capture_output=True, text=True, timeout=120)
    ok = "Coroutine.bindings.cs" in r.stdout
    check("integration: def search finds Coroutine.bindings.cs", ok, r.stdout[:200])

    r = subprocess.run([sys.executable, SCRIPT, "outline", "Transform", "--max", "30"],
                       capture_output=True, text=True, timeout=120)
    ok = "extern" in r.stdout and "position" in r.stdout
    check("integration: outline Transform lists extern members", ok, r.stdout[:200])

    r = subprocess.run([sys.executable, SCRIPT, "status"],
                       capture_output=True, text=True, timeout=120)
    check("integration: status reports root", "Root:" in r.stdout, r.stdout[:200])
else:
    print("SKIP  integration tests (no clone at %s)" % ur.default_root())

# ---------------------------------------------------------------------------
# remote integration (opt-in via UNITYREF_TEST_REMOTE=1; exercises live GitHub
# metadata + on-demand fetch with zero local source)
# ---------------------------------------------------------------------------

if os.environ.get("UNITYREF_TEST_REMOTE"):
    env = dict(os.environ, UNITYREF_FORCE_REMOTE="1")
    r = subprocess.run([sys.executable, SCRIPT, "search", "--def", "RectTransform", "--max", "3"],
                       capture_output=True, text=True, timeout=300, env=env)
    check("integration-remote: def search via tree+raw fetch",
          "RectTransform.bindings.cs" in r.stdout, r.stdout[:200])
    r = subprocess.run([sys.executable, SCRIPT, "show",
                        "Runtime/Transform/ScriptBindings/Transform.bindings.cs",
                        "--from", "26", "--to", "28"],
                       capture_output=True, text=True, timeout=300, env=env)
    check("integration-remote: show fetches on demand", "position" in r.stdout, r.stdout[:200])
    r = subprocess.run([sys.executable, SCRIPT, "tree", "Modules/UI", "--depth", "1"],
                       capture_output=True, text=True, timeout=300, env=env)
    check("integration-remote: tree from metadata", "ScriptBindings" in r.stdout, r.stdout[:200])
else:
    print("SKIP  remote integration tests (set UNITYREF_TEST_REMOTE=1 to exercise live GitHub fetch)")

# ---------------------------------------------------------------------------

print()
if failures:
    print("RESULT: %d passed, %d FAILED: %s" % (passed, len(failures), ", ".join(failures)))
    sys.exit(1)
print("RESULT: all %d checks passed" % passed)
