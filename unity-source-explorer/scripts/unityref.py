#!/usr/bin/env python3
"""unityref - explore the Unity engine C# reference source (UnityCsReference).

Single-file CLI, Python 3.8+, stdlib only.

By default works in REMOTE MODE: no Unity source code is stored locally. The
local state is only the repository address, the checked-out version, and the
repository file tree (~1 MB of metadata under ~/.unitycsref-meta). Files are
fetched on demand from raw.githubusercontent.com, scanned in memory, and never
written to disk.

If a local UnityCsReference clone exists (created via `setup`, or any clone
detected at/above the current directory), LOCAL MODE is used automatically:
full-speed search with ripgrep, offline operation, repo-wide regex search.

Repo location resolution (local mode): --root flag > UNITY_CS_REF_PATH env >
clone at/above the current directory > ~/.unitycsref.
Remote mode metadata: UNITY_CS_REF_META env > ~/.unitycsref-meta.
"""

import argparse
import concurrent.futures
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_SLUG = "Unity-Technologies/UnityCsReference"
DEFAULT_REMOTE = "https://github.com/%s.git" % REPO_SLUG
RAW_BASE = "https://raw.githubusercontent.com/%s" % REPO_SLUG
API_BASE = "https://api.github.com/repos/%s" % REPO_SLUG

ENV_PATH = "UNITY_CS_REF_PATH"
ENV_META = "UNITY_CS_REF_META"
ENV_TOKEN = "GITHUB_TOKEN"
ENV_FORCE_REMOTE = "UNITYREF_FORCE_REMOTE"
DEFAULT_DEST = "~/.unitycsref"
DEFAULT_META = "~/.unitycsref-meta"

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def die(msg, code=1):
    print("Error: " + msg, file=sys.stderr)
    sys.exit(code)


def default_root():
    return os.path.expanduser(os.environ.get(ENV_PATH, DEFAULT_DEST))


def meta_dir():
    return os.path.expanduser(os.environ.get(ENV_META, DEFAULT_META))


def is_unitycs_repo(path):
    cfg = os.path.join(path, ".git", "config")
    if not os.path.isfile(cfg):
        return False
    try:
        with open(cfg, "r", errors="replace") as fh:
            return "unitycsreference" in fh.read().lower()
    except OSError:
        return False


def clone_root(cli_root=None):
    """Return the path of a usable local clone, or None (remote mode)."""
    if os.environ.get(ENV_FORCE_REMOTE):
        return None
    if cli_root:
        if not is_unitycs_repo(cli_root):
            die("%s is not a UnityCsReference clone (missing .git/config or wrong remote)" % cli_root)
        return cli_root
    env = os.environ.get(ENV_PATH)
    if env and is_unitycs_repo(env):
        return env
    d = os.getcwd()
    for _ in range(10):
        if is_unitycs_repo(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    d = default_root()
    if is_unitycs_repo(d):
        return d
    return None


def run_git(root, args, check=True, timeout=900):
    cmd = ["git", "-C", root] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        die("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        die("git %s timed out after %ss" % (" ".join(args[:2]), timeout))
    if check and r.returncode != 0:
        die("git %s failed: %s" % (" ".join(args[:2]), (r.stderr or r.stdout).strip()[:400]))
    return r


def current_ref(root):
    branch = run_git(root, ["branch", "--show-current"], check=False).stdout.strip()
    if branch:
        return ("branch", branch)
    r = run_git(root, ["describe", "--tags", "--exact-match", "HEAD"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        return ("tag", r.stdout.strip())
    return ("detached", run_git(root, ["rev-parse", "--short", "HEAD"]).stdout.strip())


def readme_version(root):
    try:
        with open(os.path.join(root, "README.md"), "r", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return None
    m = re.match(r"##\s*Unity\s+([0-9][0-9A-Za-z.\-]*)", first)
    return m.group(1) if m else None


def era(root):
    if os.path.isdir(os.path.join(root, "ModuleOverrides")):
        return "legacy layout with ModuleOverrides/ (2021-2022 era)"
    if os.path.isdir(os.path.join(root, "Runtime", "Export", "Scripting")):
        return "modern layout: Runtime/Export core types + Modules/<Name>/{Managed,ScriptBindings}"
    return "unknown layout (open the repo and inspect root dirs)"


def fmt_ref(root):
    kind, ref = current_ref(root)
    sha = run_git(root, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    when = run_git(root, ["log", "-1", "--format=%cs"]).stdout.strip()
    ver = readme_version(root)
    s = "%s %s @ %s (%s)" % (kind, ref, sha, when)
    if ver:
        s += " [README declares Unity %s]" % ver
    return s


# ---------------------------------------------------------------------------
# version tag handling
# ---------------------------------------------------------------------------


def parse_ls_tags(out):
    tags = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            name = name[:-3]
        tags.add(name)
    return tags


def parse_ls_heads(out):
    heads = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            heads.add(parts[1][len("refs/heads/"):])
    return heads


def vkey(tag):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)([abfp])(\d+)$", tag)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                {"p": 0, "a": 1, "b": 2, "f": 3}[m.group(4)], int(m.group(5)))
    m = re.match(r"(\d+)\.(\d+)\.(\d+)$", tag)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 3, 0)
    return (0, 0, 0, -1, 0)


def ls_remote(root_or_url, kind, timeout=120):
    url = root_or_url
    if os.path.isdir(os.path.join(root_or_url, ".git")):
        url = run_git(root_or_url, ["remote", "get-url", "origin"], check=False).stdout.strip() or DEFAULT_REMOTE
    try:
        r = subprocess.run(["git", "ls-remote", "--" + kind, url],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        die("git ls-remote timed out (network problem? try --remote with a mirror URL)")
    if r.returncode != 0:
        die("git ls-remote failed: %s" % (r.stderr or "").strip()[:300])
    return r.stdout


def difflib_get_close(want, candidates):
    return difflib.get_close_matches(want, candidates, n=3, cutoff=0.5)


def resolve_target(url_or_root, want):
    """Resolve a user ref to ('branch'|'tag', name). Works for both modes."""
    heads = parse_ls_heads(ls_remote(url_or_root, "heads"))
    if want in heads:
        return ("branch", want)
    tags = parse_ls_tags(ls_remote(url_or_root, "tags"))
    vtags = {t for t in tags if re.match(r"\d+\.\d+\.\d+", t)}
    if want == "latest":
        return ("tag", max(vtags, key=vkey))
    if want in ("latest-stable", "stable"):
        ftags = [t for t in vtags if re.search(r"f\d+$", t)]
        if not ftags:
            die("no stable (f-suffix) tags found")
        return ("tag", max(ftags, key=vkey))
    if want in vtags or want in tags:
        return ("tag", want)
    pre = [t for t in vtags if t.startswith(want + ".") or t.startswith(want)]
    fpre = [t for t in pre if re.search(r"f\d+$", t)]
    if fpre or pre:
        return ("tag", max(fpre or pre, key=vkey))
    close = difflib_get_close(want, sorted(vtags))
    hint = ("did you mean: " + ", ".join(close)) if close else "run: unityref versions"
    die("unknown ref %r - %s" % (want, hint))


# ---------------------------------------------------------------------------
# remote mode: metadata, file tree, on-demand fetch
# ---------------------------------------------------------------------------


def sanitize_ref(ref):
    return re.sub(r"[^A-Za-z0-9._-]", "_", ref)


def config_ref():
    try:
        with open(os.path.join(meta_dir(), "config.json"), "r") as fh:
            return json.load(fh).get("ref", "master")
    except (OSError, ValueError):
        return "master"


def save_config(ref):
    os.makedirs(meta_dir(), exist_ok=True)
    with open(os.path.join(meta_dir(), "config.json"), "w") as fh:
        json.dump({"ref": ref, "remote": DEFAULT_REMOTE}, fh, indent=1)


def http_get(url, timeout=30):
    headers = {"User-Agent": "unityref", "Accept": "application/vnd.github+json"}
    tok = os.environ.get(ENV_TOKEN)
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            die("not found upstream: %s (check the path and the checked-out ref)" % url.split("github.com", 1)[-1])
        if e.code in (403, 429):
            die("GitHub API rate limit hit - set %s, or wait an hour "
                "(raw file fetches are not affected, only the trees API counts)" % ENV_TOKEN)
        die("HTTP %d for %s" % (e.code, url))
    except urllib.error.URLError as e:
        die("network error: %s" % e.reason)


def fetch_tree(ref, refresh=False):
    """The 'file organization' of the repo, as a local cache (~1 MB per ref).
    Returns a list of [path, size]. No source content is ever stored."""
    trees = os.path.join(meta_dir(), "trees")
    os.makedirs(trees, exist_ok=True)
    path = os.path.join(trees, sanitize_ref(ref) + ".json")
    if os.path.isfile(path) and not refresh:
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
            if data.get("ref") == ref:
                return data["files"]
        except (OSError, ValueError):
            pass
    url = API_BASE + "/git/trees/" + urllib.parse.quote(ref) + "?recursive=1"
    j = json.loads(http_get(url).decode("utf-8", "replace"))
    files = [[e["path"], e.get("size", 0)] for e in j.get("tree", []) if e.get("type") == "blob"]
    with open(path, "w") as fh:
        json.dump({"ref": ref, "truncated": bool(j.get("truncated")), "files": files}, fh)
    total = sum(sz for _, sz in files) / 1e6
    print("Fetched file tree for %s: %d files (~%.1f MB upstream) - metadata cached, no source stored" %
          (ref, len(files), total))
    if j.get("truncated"):
        print("Warning: upstream tree was truncated for this ref; results may be incomplete "
              "(a local clone via `setup` gives the full picture).", file=sys.stderr)
    return files


def raw_fetch(path, ref):
    url = RAW_BASE + "/" + urllib.parse.quote(ref) + "/" + urllib.parse.quote(path, safe="/")
    return http_get(url).decode("utf-8", "replace")


def fetch_many(paths, ref, workers=8, timeout=30):
    """Fetch several files on demand. Content stays in memory only."""
    if not paths:
        return {}
    out = {}
    if len(paths) == 1 or workers <= 1:
        for p in paths:
            try:
                out[p] = raw_fetch(p, ref)
            except SystemExit:
                out[p] = None
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(paths))) as ex:
        futs = {ex.submit(raw_fetch, p, ref): p for p in paths}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                out[p] = fut.result()
            except SystemExit:
                out[p] = None
    return out


def gh_code_search(term, cap=100):
    """Repo-wide file search via GitHub's code search API (needs GITHUB_TOKEN)."""
    if not os.environ.get(ENV_TOKEN):
        die("repo-wide remote search without --path needs %s set (unauthenticated code search is not allowed)" % ENV_TOKEN)
    url = (API_BASE + "/search/code?q=" +
           urllib.parse.quote("%s repo:%s" % (term, REPO_SLUG)) +
           "&per_page=%d" % cap)
    j = json.loads(http_get(url).decode("utf-8", "replace"))
    return [it["path"] for it in j.get("items", [])]


def path_matches(rel, fn, glob):
    if not glob:
        return True
    if "/" in glob:
        return re.fullmatch(glob_to_regex(glob), rel) is not None
    return re.match(glob_to_regex(glob) + "$", fn) is not None


def filter_paths(files, glob):
    out = []
    for p, sz in files:
        fn = p.rsplit("/", 1)[-1]
        if path_matches(p, fn, glob):
            out.append((p, sz))
    return out


def type_candidates(files, name, cap=20):
    """Filename-driven discovery of a type's definition files, using the
    repository's naming conventions (TypeName.cs / TypeName.bindings.cs /
    UnityEngineTypeName.bindings.cs)."""
    exact, near = [], []
    for p, sz in files:
        if not p.endswith(".cs"):
            continue
        fn = p.rsplit("/", 1)[-1][:-3]           # strip .cs
        stem = fn.split(".")[0]
        if fn == name or stem == name:
            exact.append(p)
        elif stem.startswith(name) or stem.endswith(name):
            near.append((len(stem), len(p), p))
    near.sort()
    cands = exact + [p for _, _, p in near]
    return cands[:cap]


def era_from_tree(files):
    paths = (p for p, _ in files)
    for p in paths:
        if p.startswith("ModuleOverrides/"):
            return "legacy layout with ModuleOverrides/ (2021-2022 era)"
    for p, _ in files:
        if p.startswith("Runtime/Export/Scripting/"):
            return "modern layout: Runtime/Export core types + Modules/<Name>/{Managed,ScriptBindings}"
    return "unknown layout"


# ---------------------------------------------------------------------------
# search patterns (shared by local and remote modes)
# ---------------------------------------------------------------------------

MODS = ("public|private|protected|internal|static|virtual|override|abstract|"
        "sealed|extern|new|async|unsafe|partial|readonly|ref|const|volatile|"
        "implicit|explicit|fixed|required")
MODIFIER_SET = set(MODS.split("|"))
TYPE_TOKEN = r"[A-Za-z_][\w.]*(?:<(?:[^<>]|<[^<>]*>)*>)?(?:\s*\[[^\]]*\])?"

NON_TYPE_WORDS = set(MODS.split("|")) | {
    "return", "yield", "await", "case", "break", "using", "throw", "else", "do",
    "in", "is", "as", "get", "set", "add", "remove", "this", "base", "where",
    "select", "from", "when", "not", "and", "or", "default", "var", "namespace",
    "class", "struct", "interface", "enum", "record", "if", "while", "for",
    "foreach", "switch", "try", "catch", "finally", "lock", "goto", "continue",
    "sizeof", "typeof", "nameof", "null", "true", "false", "operator", "params",
    "out", "value", "global", "event", "delegate", "object", "string",
}


def glob_to_regex(glob):
    i, out = 0, ""
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i:i + 3] == "**/":
                out += "(?:.*/)?"
                i += 3
                continue
            if glob[i:i + 2] == "**":
                out += ".*"
                i += 2
                continue
            out += "[^/]*"
            i += 1
            continue
        if c == "?":
            out += "[^/]"
            i += 1
            continue
        out += re.escape(c)
        i += 1
    return out


def def_pattern(name):
    esc = re.escape(name)
    type_decl = r"\b(?:class|struct|interface|enum|record)\s+" + esc + r"\b"
    delegate_decl = r"\bdelegate\s+[\w<>\[\],. ]+?\s+" + esc + r"\s*\("
    return "(?:%s|%s)" % (type_decl, delegate_decl)


def symbol_pattern(name):
    esc = re.escape(name)
    return (r"^[ \t]*(?:(?:" + MODS + r")\s+)*(?:" + TYPE_TOKEN + r"\s+)?"
            + esc + r"\b\s*(?:<[^<>]*>)?\s*(?:\(|\{|;|=>|=)")


def decl_prefix_ok(prefix):
    toks = prefix.split()
    if not toks:
        return False
    if any(t in MODIFIER_SET for t in toks):
        return True
    last = toks[-1]
    if last in NON_TYPE_WORDS:
        return False
    return re.fullmatch(TYPE_TOKEN, last) is not None


def line_is_decl(line, name_re, name):
    if re.search(r"\boperator\b", line):
        return False
    if re.search(r"\b(?:class|struct|interface|enum|record|namespace|delegate)\s+" + re.escape(name) + r"\b", line):
        return False
    m = name_re.search(line)
    if not m:
        return False
    return decl_prefix_ok(line[:m.start()])


def rg_available():
    return shutil.which("rg") is not None


def scan_text(text, cre, symbol_name=None):
    """Scan one file's in-memory text; return [(lineno, line)]."""
    hits = []
    name_re = re.compile(re.escape(symbol_name)) if symbol_name else None
    for i, line in enumerate(text.splitlines(), 1):
        if cre.search(line):
            if symbol_name and not line_is_decl(line, name_re, symbol_name):
                continue
            hits.append((i, line[:300]))
    return hits


# ---------------------------------------------------------------------------
# local-mode search backends (used when a clone exists)
# ---------------------------------------------------------------------------


def _search_rg(root, pattern, icase, cs_only, path_glob, max_store):
    args = ["rg", "-n", "--no-heading", "--color", "never", "--no-ignore",
            "--max-columns", "300"]
    if icase:
        args.append("-i")
    if path_glob:
        args += ["-g", path_glob]
    args += ["-e", pattern, "."]
    try:
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        die("rg timed out; aborting")
    if r.returncode not in (0, 1):
        die("rg failed (rc=%d): %s" % (r.returncode, (r.stderr or "").strip()[:300]))
    matches, files = [], set()
    total = 0
    for raw in r.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) != 3:
            continue
        rel, lineno, text = parts[0].replace("\\", "/"), parts[1], parts[2]
        if rel.startswith("./"):
            rel = rel[2:]
        if cs_only and not rel.endswith(".cs"):
            continue
        total += 1
        files.add(rel)
        if len(matches) < max_store:
            matches.append((rel, int(lineno) if lineno.isdigit() else 0, text))
    return matches, total, len(files), total > len(matches)


def _search_py(root, pattern, icase, cs_only, path_glob, max_store):
    flags = re.IGNORECASE if icase else 0
    try:
        cre = re.compile(pattern, flags)
    except re.error as e:
        die("invalid regex %r: %s" % (pattern, e))
    base_re = path_re = None
    if path_glob:
        if "/" in path_glob:
            path_re = re.compile(glob_to_regex(path_glob))
        else:
            base_re = re.compile(glob_to_regex(path_glob) + r"$")
    matches, files = [], set()
    total = 0
    counted_cap = 200000
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if cs_only and not fn.endswith(".cs"):
                continue
            if base_re is not None and not base_re.match(fn):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if path_re is not None and not path_re.fullmatch(rel):
                continue
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if b"\x00" in data[:65536]:
                continue
            text = data.decode("utf-8", "replace")
            for i, line in enumerate(text.splitlines(), 1):
                if cre.search(line):
                    total += 1
                    files.add(rel)
                    if len(matches) < max_store:
                        matches.append((rel, i, line[:300]))
            if total >= counted_cap:
                break
        if total >= counted_cap:
            break
    return matches, total, len(files), total > len(matches)


def search_backend(root, pattern, icase, cs_only, path_glob, max_store):
    if rg_available() and not os.environ.get("UNITYREF_NO_RG"):
        return _search_rg(root, pattern, icase, cs_only, path_glob, max_store)
    return _search_py(root, pattern, icase, cs_only, path_glob, max_store)


def def_search_files(root, type_name, icase=False):
    matches, total, nfiles, _ = search_backend(root, def_pattern(type_name), icase, True, None, 50)
    files = {}
    for rel, line, text in matches:
        files.setdefault(rel, line)
    return files


def print_matches(matches, total, nfiles, max_shown, label, footer):
    if not matches:
        print("No matches found (%s)." % label)
        print("Hints: try --def / --symbol / --regex, add -i for case-insensitive,")
        print("       or widen/narrow --path. 'unityref outline <Type>' shows structure.")
        return
    for rel, line, text in matches[:max_shown]:
        print("%s:%d:%s" % (rel, line, text))
    if total > max_shown:
        print("... %d matches total in %d files (showing first %d; raise --max for more)"
              % (total, nfiles, max_shown))
    else:
        print("%d match%s in %d file%s" % (total, "" if total == 1 else "es",
                                           nfiles, "" if nfiles == 1 else "s"))
    print(footer)


# ---------------------------------------------------------------------------
# outline (shared renderer for both modes)
# ---------------------------------------------------------------------------

RE_NS = re.compile(r"^namespace\s+([\w.@]+)")
RE_TYPE = re.compile(r"^(?:(?:" + MODS + r")\s+)*(class|struct|interface|enum|record)\s+([A-Za-z_]\w*)")
RE_DELEGATE = re.compile(r"^(?:(?:" + MODS + r")\s+)*delegate\s+" + TYPE_TOKEN + r"\s+([A-Za-z_]\w*)\s*(?:<[^<>]*>)?\s*\(")
RE_METHOD = re.compile(r"^(?:(?:" + MODS + r")\s+)*(?:(" + TYPE_TOKEN + r")\s+)?([A-Za-z_]\w*)\s*(?:<[^<>]*>)?\s*\(")
RE_PROP = re.compile(r"^(?:(?:" + MODS + r")\s+)*(?:(" + TYPE_TOKEN + r")\s+)?([A-Za-z_]\w*)\s*(?:<[^<>]*>)?\s*(?:\{|=>)")
RE_EVENT = re.compile(r"^(?:(?:" + MODS + r")\s+)*event\s+(?:" + TYPE_TOKEN + r"\s+)?([A-Za-z_]\w*)\s*(?:=|;)")
RE_FIELD = re.compile(r"^(?:(?:" + MODS + r")\s+)*(" + TYPE_TOKEN + r")\s+([A-Za-z_]\w*)\s*(?:\[\s*\])?\s*(?:=|;)")
RE_ENUM_MEMBER = re.compile(r"^([A-Za-z_]\w*)\s*(?:=.*)?[,]?$")
RE_ATTR_LINE = re.compile(r"^(\[.*\])\s*(.*)$")
RE_NATIVE_ATTR = re.compile(r"Native|FreeFunction|ThreadSafe|Wrapperless|UnmanagedFunction")
RE_SERIALIZED_ATTR = re.compile(r"SerializeField|FormerlySerializedAs")


def strip_comments_strings(text):
    """Blank out comments and string/char literals, preserving newlines."""
    out = []
    i, n = 0, len(text)
    state = "code"
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"
                out.append("  ")
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block"
                out.append("  ")
                i += 2
                continue
            if c == '"':
                j = i - 1
                while j >= 0 and text[j] in "@$":
                    j -= 1
                verbatim = text[j + 1:i].endswith("@")
                state = "verb" if verbatim else "str"
                out.append(" ")
                i += 1
                continue
            if c == "'":
                state = "chr"
                out.append(" ")
                i += 1
                continue
            out.append(c)
            i += 1
        elif state == "line":
            out.append("\n" if c == "\n" else " ")
            i += 1
            if c == "\n":
                state = "code"
        elif state == "block":
            if c == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        elif state == "str":
            if c == "\\":
                out.append("  ")
                i += 2
            else:
                if c == '"' or c == "\n":
                    state = "code"
                out.append("\n" if c == "\n" else " ")
                i += 1
        elif state == "verb":
            if c == '"' and nxt == '"':
                out.append("  ")
                i += 2
            else:
                if c == '"':
                    state = "code"
                out.append("\n" if c == "\n" else " ")
                i += 1
        else:  # chr
            if c == "\\":
                out.append("  ")
                i += 2
            else:
                if c == "'" or c == "\n":
                    state = "code"
                out.append(" " if c != "\t" else "\t")
                i += 1
    return "".join(out)


def parse_outline(text):
    stripped = strip_comments_strings(text)
    lines = stripped.split("\n")
    entries = []
    counts = {"namespace": 0, "type": 0, "enum": 0, "delegate": 0, "method": 0,
              "property": 0, "field": 0, "event": 0, "enum-member": 0}
    stack = []
    pending = None
    pending_attrs = []
    depth = 0

    for idx, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        opens = s.count("{")
        closes = s.count("}")
        attrs_here = pending_attrs
        pending_attrs = []

        if s.startswith("["):
            m = RE_ATTR_LINE.match(s)
            if m and m.group(2):
                attrs_here = attrs_here + [m.group(1)]
                s = m.group(2).strip()
            else:
                pending_attrs.append(s)
                continue

        kind = name = None
        top = stack[-1] if stack else None
        at_member_depth = top is not None and depth == top[2] + 1

        m = RE_NS.match(s)
        if m:
            kind, name = "namespace", m.group(1)
        elif RE_DELEGATE.match(s):
            m2 = RE_DELEGATE.match(s)
            kind, name = "delegate", m2.group(1)
        elif m is None:
            m2 = RE_TYPE.match(s)
            if m2:
                kind = "enum" if m2.group(1) == "enum" else "type"
                name = m2.group(2)
            elif at_member_depth and top[0] == "enum":
                m3 = RE_ENUM_MEMBER.match(s)
                if m3:
                    kind, name = "enum-member", m3.group(1)
            elif at_member_depth:
                if RE_METHOD.match(s):
                    m3 = RE_METHOD.match(s)
                    ret_t = m3.group(1)
                    cand = m3.group(2)
                    if ret_t is None and top[0] in ("type", "enum") and cand == top[1]:
                        kind, name = "method", cand          # constructor
                    else:
                        kind, name = "method", cand
                elif RE_PROP.match(s):
                    kind, name = "property", RE_PROP.match(s).group(2)
                elif RE_EVENT.match(s):
                    kind, name = "event", RE_EVENT.match(s).group(1)
                elif RE_FIELD.match(s):
                    kind, name = "field", RE_FIELD.match(s).group(2)

        if kind is None:
            if opens and pending:
                stack.append([pending[0], pending[1], pending[2]])
                pending = None
            depth += opens - closes
            while stack and stack[-1][2] >= depth:
                stack.pop()
            continue

        decl = re.sub(r"\s*\{\s*\}\s*$", "", s)
        decl = re.sub(r"\s*\{\s*$", "", decl)
        decl = re.sub(r"\s{2,}", " ", decl)
        markers = []
        if re.search(r"\bextern\b", decl):
            markers.append("extern")
        joined = " ".join(attrs_here)
        if RE_NATIVE_ATTR.search(joined):
            markers.append("native")
        if RE_SERIALIZED_ATTR.search(joined) or RE_SERIALIZED_ATTR.search(decl):
            markers.append("serialized")

        entries.append((idx, depth, kind, name, decl[:140], markers, len(attrs_here)))
        counts[kind] = counts.get(kind, 0) + 1

        enters = opens > 0 and (closes == 0 or s.index("{") < s.index("}"))
        if kind in ("namespace", "type", "enum"):
            if enters:
                stack.append([kind, name, depth])
            else:
                pending = (kind, name, depth)

        depth += opens - closes
        while stack and stack[-1][2] >= depth:
            stack.pop()
        if depth < 0:
            depth = 0

    return entries, counts


def render_outline(rel, text, args):
    entries, counts = parse_outline(text)
    nlines = text.count("\n") + 1
    print("Outline of %s (%d lines)" % (rel, nlines))
    if rel.endswith(".bindings.cs"):
        print("  (bindings file: extern members are backed by native engine code - see conventions.md)")
    filt = None
    if args.filter:
        try:
            filt = re.compile(args.filter)
        except re.error as e:
            die("invalid --filter regex: %s" % e)
    shown = 0
    for (ln, depth, kind, name, decl, markers, nattrs) in entries:
        header = kind in ("namespace", "type", "enum")
        if args.types_only and not header:
            continue
        if filt is not None and not header and not filt.search(name):
            continue
        if shown >= args.max:
            print("... output capped at %d entries (use --max / --filter / --types-only)" % args.max)
            break
        pad = "  " * (depth if depth < 6 else 5)
        mark = (" [" + ",".join(markers) + "]") if markers else ""
        extra = (" (+" + str(nattrs - 1) + " attrs)") if nattrs > 1 else ""
        print("L%-6d %s%s%s%s" % (ln, pad, decl, mark, extra))
        shown += 1
    print("summary: %(namespace)s namespace, %(type)s type, %(enum)s enum, %(delegate)s delegate, "
          "%(method)s method, %(property)s property, %(field)s field, "
          "%(event)s event, %(enum-member)s enum member" % counts)
    print("L-numbers are 1-based line numbers - read the file at those lines for full context.")


def resolve_outline_target_local(root, arg):
    arg = arg.strip()
    if "/" in arg or arg.endswith(".cs"):
        p = arg if os.path.isabs(arg) else os.path.join(root, arg)
        if os.path.isfile(p):
            return os.path.relpath(p, root).replace(os.sep, "/")
        die("no such file: %s (paths are repo-relative, e.g. Modules/UIElements/Core/VisualElement.cs)" % arg)
    files = def_search_files(root, arg)
    if not files:
        die("no type named %r found (try: unityref search --def %s --regex)" % (arg, arg))
    if len(files) == 1:
        return list(files.keys())[0]
    print("Multiple files declare %r - pass one of these paths to outline:" % arg)
    for rel, line in sorted(files.items()):
        print("  %s:%d" % (rel, line))
    die("ambiguous type name", 2)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def short_stats(root):
    r = run_git(root, ["ls-files"], check=False)
    all_files = r.stdout.splitlines()
    ncs = sum(1 for f in all_files if f.endswith(".cs"))
    return len(all_files), ncs


def cmd_status(args):
    root = clone_root(args.root)
    if root:
        total, ncs = short_stats(root)
        du = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for fn in filenames:
                try:
                    du += os.stat(os.path.join(dirpath, fn)).st_size
                except OSError:
                    pass
        remote = run_git(root, ["remote", "get-url", "origin"], check=False).stdout.strip()
        print("Mode:     local clone (full-speed search, offline, repo-wide regex)")
        print("Root:     %s" % root)
        print("Remote:   %s" % (remote or "(none)"))
        print("Checkout: %s" % fmt_ref(root))
        print("Era:      %s" % era(root))
        print("Files:    %d tracked, %d C# files" % (total, ncs))
        print("Disk:     %.0f MB (working tree)" % (du / 1e6))
        print("Tip:      unityref search --def <Type> | outline <Type> | tree Modules")
        return
    ref = config_ref()
    files = fetch_tree(ref)
    ncs = sum(1 for p, _ in files if p.endswith(".cs"))
    total_mb = sum(sz for _, sz in files) / 1e6
    print("Mode:     remote (no Unity source stored locally)")
    print("Repo:     %s" % DEFAULT_REMOTE)
    print("Ref:      %s" % ref)
    print("Era:      %s" % era_from_tree(files))
    print("Tree:     %d files, %d C# (~%.0f MB upstream) - paths+sizes cached at %s/trees/" %
          (len(files), ncs, total_mb, meta_dir()))
    print("Note:     repo-wide text/symbol search needs --path scoping, %s, or a local clone (setup)" % ENV_TOKEN)
    print("Tip:      unityref use 2022.3 | search --def <Type> | outline <Type> | show <path>")


def cmd_where(args):
    root = clone_root(args.root)
    if root:
        print(root)
        print("mode: local clone; checked out: %s" % fmt_ref(root))
        print("native file tools may read this path directly")
    else:
        print("remote mode - no local source copy")
        print("metadata: %s (repo address + file trees only)" % meta_dir())
        print("read files with: unityref show <path> [--from N --to M]")


def cmd_setup(args):
    dest = os.path.expanduser(args.dest or os.environ.get(ENV_PATH) or DEFAULT_DEST)
    if is_unitycs_repo(dest):
        print("Already set up at %s" % dest)
        print("Checkout: %s" % fmt_ref(dest))
        print("Refresh with: python3 unityref.py update [--root %s]" % dest)
        return
    if os.path.exists(dest) and os.listdir(dest):
        die("%s exists and is not an empty directory" % dest)
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    remote = args.remote or DEFAULT_REMOTE
    cmd = ["git", "clone", "--depth", "1"]
    if args.ref:
        cmd += ["--branch", args.ref]
    cmd += [remote, dest]
    print("Cloning (shallow) %s" % remote)
    if args.ref:
        print("  at ref: %s" % args.ref)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        die("git executable not found on PATH")
    except subprocess.TimeoutExpired:
        die("clone timed out after 1800s")
    if r.returncode != 0:
        die("clone failed: %s" % (r.stderr or r.stdout).strip()[:400])
    total, ncs = short_stats(dest)
    print("Success: cloned to %s" % dest)
    print("Checkout: %s" % fmt_ref(dest))
    print("          %d tracked files, %d C# sources" % (total, ncs))
    print("Note: this is the OPTIONAL accelerator - remote mode already works without it,")
    print("      and the clone is now auto-preferred whenever it exists.")
    if dest != default_root():
        print("Either export %s=%s or always pass --root %s" % (ENV_PATH, dest, dest))


def cmd_update(args):
    root = clone_root(args.root)
    if root:
        kind, ref = current_ref(root)
        if kind == "branch":
            run_git(root, ["fetch", "--depth", "1", "origin", ref])
            run_git(root, ["reset", "--hard", "FETCH_HEAD"])
        elif kind == "tag":
            run_git(root, ["fetch", "--depth", "1", "origin", "tag", ref])
            run_git(root, ["reset", "--hard", "FETCH_HEAD"])
        else:
            die("HEAD is detached at an unknown ref; re-select with: unityref use <tag>")
        print("Updated %s %s" % (kind, ref))
        print("Checkout: %s" % fmt_ref(root))
        return
    ref = config_ref()
    files = fetch_tree(ref, refresh=True)
    print("Refreshed file tree for %s: %d files" % (ref, len(files)))


def cmd_versions(args):
    root = None
    if not args.root and os.path.isdir(resolve_root_default()) and is_unitycs_repo(resolve_root_default()):
        root = resolve_root_default()
    elif args.root and is_unitycs_repo(args.root):
        root = args.root
    url = args.remote or root or DEFAULT_REMOTE
    tags = {t for t in parse_ls_tags(ls_remote(url, "tags")) if re.match(r"\d+\.\d+\.\d+", t)}
    if not tags:
        die("no version tags parsed from ls-remote output")
    heads = parse_ls_heads(ls_remote(url, "heads"))
    if args.stream:
        pre = sorted((t for t in tags if t.startswith(args.stream)), key=vkey)
        if not pre:
            die("no tags matching stream %r" % args.stream)
        print("Tags matching %r (oldest first):" % args.stream)
        for t in pre[-args.limit:]:
            print("  " + t)
        print("total: %d" % len(pre))
        return
    newest = sorted(tags, key=vkey, reverse=True)
    print("Upstream tags: %d total. Newest first:" % len(tags))
    for t in newest[:args.limit]:
        print("  " + t)
    streams = {}
    for t in tags:
        m = re.match(r"(\d+\.\d+)\.", t)
        if m:
            streams.setdefault(m.group(1), []).append(t)
    tops = sorted(streams.items(), key=lambda kv: vkey(max(kv[1], key=vkey)), reverse=True)
    print("Streams (major.minor -> newest tag):")
    for name, lst in tops[:8]:
        best = max(lst, key=vkey)
        lts = " (LTS)" if name in ("2022.3", "2021.3", "2020.3", "2019.4") else ""
        print("  %s -> %s%s" % (name, best, lts))
    if heads:
        print("Maintenance branches: %d (one per release line, e.g. 2022.3, 6000.6, master)" % len(heads))
    print("Switch: unityref use <tag> | use latest | use latest-stable | use <line> (branch tip)")


def resolve_root_default():
    return clone_root() or default_root()


def cmd_use(args):
    root = clone_root(args.root)
    if root:
        target_kind, target = resolve_target(root, args.ref)
        if target_kind == "branch":
            run_git(root, ["fetch", "--depth", "1", "origin", target])
            r = run_git(root, ["rev-parse", "--verify", target], check=False)
            if r.returncode == 0:
                run_git(root, ["checkout", target])
                run_git(root, ["reset", "--hard", "FETCH_HEAD"])
            else:
                run_git(root, ["checkout", "-b", target, "FETCH_HEAD"])
        else:
            have = run_git(root, ["rev-parse", "--verify", "tags/" + target], check=False)
            if have.returncode != 0:
                print("Fetching tag %s (shallow, may take a while)..." % target)
                run_git(root, ["fetch", "--depth", "1", "origin", "tag", target])
            run_git(root, ["checkout", "--detach", "tags/" + target])
        total, ncs = short_stats(root)
        print("Success: now on %s %s" % (target_kind, target))
        print("Checkout: %s" % fmt_ref(root))
        print("Era:      %s" % era(root))
        print("          %d tracked files, %d C# sources" % (total, ncs))
        return
    if re.match(r"\d+\.\d+\.\d+[abfp]\d+$", args.ref):
        # full-version tag: skip validation entirely, the tree fetch is the check
        target_kind, target = "tag", args.ref
    else:
        # branches hit the fast heads listing; prefix/latest fall through to tags
        target_kind, target = resolve_target(DEFAULT_REMOTE, args.ref)
    save_config(target)
    try:
        files = fetch_tree(target, refresh=True)
    except SystemExit:
        # ref looked complete but does not exist upstream - resolve with suggestions
        target_kind, target = resolve_target(DEFAULT_REMOTE, args.ref)
        save_config(target)
        files = fetch_tree(target, refresh=True)
    ncs = sum(1 for p, _ in files if p.endswith(".cs"))
    print("Success: now using %s %s (remote mode - no source downloaded, switched instantly)" %
          (target_kind, target))
    print("Tree:     %d files, %d C# sources" % (len(files), ncs))
    print("Era:      %s" % era_from_tree(files))


# --- search ---------------------------------------------------------------


def build_search_pattern(args):
    if args.mode == "def":
        return def_pattern(args.pattern), True
    if args.mode == "symbol":
        return symbol_pattern(args.pattern), True
    if args.mode == "regex":
        return args.pattern, False
    return re.escape(args.pattern), False


def cmd_search(args):
    root = clone_root(args.root)
    icase = bool(args.ignore_case)
    pattern, cs_only = build_search_pattern(args)
    label = "%s=%r%s" % (args.mode, args.pattern, " -i" if icase else "")
    if root:
        matches, total, nfiles, truncated = search_backend(
            root, pattern, icase, cs_only, args.path, args.max)
        if args.mode == "symbol":
            name_re = re.compile(re.escape(args.pattern))
            matches = [m for m in matches if line_is_decl(m[2], name_re, args.pattern)]
            total = len(matches)
        print_matches(matches, total, nfiles, args.max, label, "repo: %s" % root)
        return
    # remote mode
    ref = config_ref()
    files = fetch_tree(ref)
    symbol_name = args.pattern if args.mode == "symbol" else None
    try:
        cre = re.compile(pattern, re.IGNORECASE if icase else 0)
    except re.error as e:
        die("invalid pattern: %s" % e)
    out = []
    if args.mode == "def":
        cands = type_candidates(files, args.pattern, cap=20)
        if args.path:
            cands = [p for p, _ in filter_paths([[p, 0] for p in cands], args.path)]
        texts = fetch_many(cands[:20], ref)
        for p in cands:
            t = texts.get(p)
            if t is None:
                continue
            for i, line in enumerate(t.splitlines(), 1):
                if cre.search(line):
                    out.append((p, i, line))
        if not out and os.environ.get(ENV_TOKEN):
            for p in gh_code_search(args.pattern)[:args.max_files]:
                try:
                    t = raw_fetch(p, ref)
                except SystemExit:
                    continue
                for i, line in enumerate(t.splitlines(), 1):
                    if cre.search(line):
                        out.append((p, i, line))
        if not out:
            print("No declaration found for %r via filename conventions (%d candidates checked)." %
                  (args.pattern, len(cands)))
            print("Known outlier: UnityEngine.Object lives in UnityEngineObject.bindings.cs.")
            print("Try: unityref search --def %s --path 'Modules/<Area>/**', set %s for code search,"
                  " or 'setup' a local clone." % (args.pattern, ENV_TOKEN))
            return
    elif args.path:
        scoped = [p for p, _ in filter_paths(files, args.path) if p.endswith(".cs")]
        if not scoped:
            die("--path %r matched no .cs files in the %s tree" % (args.path, ref))
        if len(scoped) > args.max_files:
            print("Note: %d files match --path; scanning first %d (raise --max-files)" %
                   (len(scoped), args.max_files))
            scoped = scoped[:args.max_files]
        texts = fetch_many(scoped, ref)
        for p in scoped:
            t = texts.get(p)
            if t is None:
                continue
            out.extend((p, i, l) for i, l in scan_text(t, cre, symbol_name))
    else:
        paths = gh_code_search(args.pattern if args.mode == "text" else args.pattern)
        if not paths:
            print("No files found upstream for %r." % args.pattern)
            return
        paths = [p for p in paths if p.endswith(".cs")][:args.max_files]
        texts = fetch_many(paths, ref)
        for p in paths:
            t = texts.get(p)
            if t is None:
                continue
            out.extend((p, i, l) for i, l in scan_text(t, cre, symbol_name))
    out.sort(key=lambda m: (m[0], m[1]))
    print_matches(out, len(out), len(set(p for p, _, _ in out)), args.max, label,
                  "ref: %s (remote mode - files fetched on demand, nothing stored)" % ref)


def cmd_member(args):
    root = clone_root(args.root)
    if root:
        type_files = def_search_files(root, args.type, args.ignore_case)
        if not type_files:
            die("no type declaration found for %r (try: unityref search --def %s)" % (args.type, args.type))
        if len(type_files) > 25:
            type_files = dict(list(type_files.items())[:25])
        name_re = re.compile(re.escape(args.member), re.IGNORECASE if args.ignore_case else 0)
        pattern = symbol_pattern(args.member)
        if args.ignore_case:
            pattern = "(?i)" + pattern
        total = 0
        shown = 0
        for rel, decl_line in sorted(type_files.items()):
            full = os.path.join(root, rel)
            try:
                with open(full, "r", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            hits = []
            for i, line in enumerate(lines, 1):
                if name_re.search(line) and re.search(pattern, line) \
                        and line_is_decl(line, name_re, args.member):
                    hits.append((i, line[:200]))
            if hits:
                print("# %s (class declared at line %d)" % (rel, decl_line))
                for i, line in hits:
                    print("%s:%d:%s" % (rel, i, line))
                    total += 1
                    shown += 1
                    if shown >= args.max:
                        break
            if shown >= args.max:
                break
        if total == 0:
            print("No declaration of %r found inside the definition file(s) of %r." % (args.member, args.type))
            print("It may be an inherited member; search it broadly:")
            print("  unityref search --symbol %s" % args.member)
            return
        print("%d declaration(s) of %r within %s" % (total, args.member, args.type))
        return
    # remote mode
    ref = config_ref()
    files = fetch_tree(ref)
    cands = type_candidates(files, args.type, cap=20)
    texts = fetch_many(cands, ref)
    verified = []
    for p in cands:
        t = texts.get(p)
        if t is None:
            continue
        for i, line in enumerate(t.splitlines(), 1):
            if re.search(def_pattern(args.type), line):
                verified.append((p, i, t))
                break
    if not verified:
        die("could not locate %r by filename conventions - try 'search --def %s' or 'setup' a clone"
            % (args.type, args.type))
    name_re = re.compile(re.escape(args.member), re.IGNORECASE if args.ignore_case else 0)
    symbol_pat = symbol_pattern(args.member)
    if args.ignore_case:
        symbol_pat = "(?i)" + symbol_pat
    total = 0
    shown = 0
    for p, decl_line, t in verified[:10]:
        hits = []
        for i, line in enumerate(t.splitlines(), 1):
            if name_re.search(line) and re.search(symbol_pat, line) \
                    and line_is_decl(line, name_re, args.member):
                hits.append((i, line[:200]))
        if hits:
            print("# %s (class declared at line %d)" % (p, decl_line))
            for i, line in hits:
                print("%s:%d:%s" % (p, i, line))
                total += 1
                shown += 1
                if shown >= args.max:
                    break
        if shown >= args.max:
            break
    if total == 0:
        print("No declaration of %r found inside the definition file(s) of %r." % (args.member, args.type))
        print("It may be an inherited member; try: unityref search --symbol %s [--path ...]" % args.member)
        return
    print("%d declaration(s) of %r within %s (remote mode)" % (total, args.member, args.type))


def cmd_outline(args):
    root = clone_root(args.root)
    if root:
        rel = resolve_outline_target_local(root, args.target)
        try:
            with open(os.path.join(root, rel), "r", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            die("cannot read %s: %s" % (rel, e))
        render_outline(rel, text, args)
        return
    ref = config_ref()
    files = fetch_tree(ref)
    arg = args.target.strip()
    if "/" in arg or arg.endswith(".cs"):
        known = [p for p, _ in files if p == arg]
        if not known:
            die("no such file in the %s tree: %s (paths are repo-relative)" % (ref, arg))
        render_outline(arg, raw_fetch(arg, ref), args)
        return
    cands = type_candidates(files, arg, cap=20)
    texts = fetch_many(cands[:6], ref)
    chosen = None
    for p in cands[:6]:
        t = texts.get(p)
        if t is None:
            continue
        if re.search(def_pattern(arg), t):
            chosen = (p, t)
            break
    if chosen is None:
        if cands:
            print("No file matching %r by name conventions declares it; candidates checked:" % arg)
            for p in cands[:6]:
                print("  " + p)
        die("could not locate type %r remotely - try a path, or 'setup' a local clone" % arg)
    others = [p for p in cands if p != chosen[0]][:4]
    if others:
        print("(also declared in: %s)" % ", ".join(others))
    render_outline(chosen[0], chosen[1], args)


def cmd_show(args):
    root = clone_root(args.root)
    ref = config_ref()
    if root:
        p = args.path if os.path.isabs(args.path) else os.path.join(root, args.path)
        if not os.path.isfile(p):
            rel = args.path
            die("no such file: %s (repo-relative to %s)" % (args.path, root))
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        try:
            with open(p, "r", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            die("cannot read %s: %s" % (args.path, e))
    else:
        files = fetch_tree(ref)
        if not any(p == args.path for p, _ in files):
            die("no such file in the %s tree: %s" % (ref, args.path))
        text = raw_fetch(args.path, ref)
        rel = args.path
    lines = text.splitlines()
    start = max(1, args.start)
    end = args.end if args.end > 0 else len(lines)
    if end > len(lines):
        end = len(lines)
    if end - start + 1 > args.max:
        print("(showing %d-%d of %d lines; raise --max or narrow --from/--to)"
              % (start, start + args.max - 1, len(lines)))
        end = start + args.max - 1
    print("%s (%d lines) - showing %d..%d" % (rel, len(lines), start, end))
    for i in range(start, end + 1):
        print("%5d| %s" % (i, lines[i - 1]))


def cmd_tree(args):
    root = clone_root(args.root)
    sub = args.subpath.strip("/") if args.subpath else ""
    out = []
    truncated = False

    def emit(line):
        nonlocal truncated
        if len(out) >= args.max:
            truncated = True
            return
        out.append(line)

    if root:
        base = os.path.normpath(os.path.join(root, sub))
        if not os.path.isdir(base):
            die("no such directory: %s" % sub)

        def walk(path, rel, depth_left):
            try:
                entries = os.listdir(path)
            except OSError:
                return
            dirs = sorted(e for e in entries if os.path.isdir(os.path.join(path, e)) and e != ".git")
            files = sorted(e for e in entries if not os.path.isdir(os.path.join(path, e)))
            ncs = sum(1 for e in files if e.endswith(".cs"))
            nbind = sum(1 for e in files if e.endswith(".bindings.cs"))
            label = rel if rel else "."
            meta = []
            if ncs:
                meta.append("%d .cs" % ncs)
            if nbind:
                meta.append("%d bindings" % nbind)
            emit("%s%s (%s)" % (label, "/" if rel else "", ", ".join(meta) or "no .cs"))
            if depth_left <= 0:
                if files and (args.files or len(files) <= 25):
                    for e in files:
                        emit("  " * (1 if rel else 0) + e)
                elif files and not args.files:
                    out[-1] = out[-1] + " <%d files, --files to list>" % len(files) if out else out[-1]
                return
            for d in dirs:
                walk(os.path.join(path, d), (rel + "/" + d) if rel else d, depth_left - 1)

        walk(base, "" if not sub else sub, args.depth)
        for line in out:
            print(line)
        if truncated:
            print("... capped at %d lines (raise --max or narrow the subpath)" % args.max)
        print("repo: %s" % root)
        return
    # remote tree from cached metadata
    ref = config_ref()
    files = fetch_tree(ref)
    prefix = (sub + "/") if sub else ""

    def render(prefix_dir, depth_left, indent):
        agg = {}
        root_files = []
        for p, sz in files:
            if prefix_dir and not p.startswith(prefix_dir):
                continue
            rest = p[len(prefix_dir):]
            parts = rest.split("/")
            if len(parts) == 1:
                root_files.append((parts[0], sz))
                continue
            d = parts[0]
            rec = agg.setdefault(d, [0, 0])
            if p.endswith(".bindings.cs"):
                rec[1] += 1
            elif p.endswith(".cs"):
                rec[0] += 1
        label = prefix_dir.rstrip("/") if prefix_dir else "."
        meta = []
        ncs = sum(1 for _, _ in root_files)
        if ncs:
            meta.append("%d .cs" % ncs)
        emit("%s (%s)" % (label, ", ".join(meta) or "no .cs"))
        for d in sorted(agg):
            c, b = agg[d]
            meta = []
            if c:
                meta.append("%d .cs direct" % c)
            if b:
                meta.append("%d bindings" % b)
            emit("%s%s/ (%s)" % ("  " * indent, d, ", ".join(meta) or "no .cs direct"))
            if depth_left > 1:
                render(prefix_dir + d + "/", depth_left - 1, indent + 1)
        if root_files and (args.files or len(root_files) <= 25):
            for fn, sz in root_files:
                emit("%s  %s (%.1f KB)" % ("  " * indent, fn, sz / 1024.0))

    render(prefix, args.depth, 1)
    for line in out:
        print(line)
    if truncated:
        print("... capped at %d lines (raise --max or narrow the subpath)" % args.max)
    print("ref: %s (tree served from local metadata - no source stored)" % ref)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv):
    p = argparse.ArgumentParser(
        prog="unityref",
        description="Explore the Unity engine C# reference source (UnityCsReference). "
                    "Remote mode by default: no source code is stored locally, only the "
                    "repo address and the file tree (~1 MB metadata). A local clone "
                    "(optional, via `setup`) is auto-detected and preferred.")
    p.add_argument("--root", help="local clone path override (default: $%s, then cwd scan, then %s)"
                                 % (ENV_PATH, DEFAULT_DEST))
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    sp = sub.add_parser("status", help="mode, repo address, checked-out version, tree stats")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("where", help="print the resolved local clone path or metadata dir")
    sp.set_defaults(fn=cmd_where)

    sp = sub.add_parser("setup", help="OPTIONAL accelerator: shallow-clone the full source locally")
    sp.add_argument("--dest", help="clone destination (default: %s)" % DEFAULT_DEST)
    sp.add_argument("--remote", help="git remote URL override (e.g. a mirror)")
    sp.add_argument("--ref", help="branch or tag to clone at (e.g. 2022.3.76f1)")
    sp.set_defaults(fn=cmd_setup)

    sp = sub.add_parser("update", help="refresh the current ref (tree in remote mode, checkout in local mode)")
    sp.set_defaults(fn=cmd_update)

    sp = sub.add_parser("versions", help="list upstream Unity version tags")
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument("--stream", help="only tags with this prefix, e.g. 2022.3")
    sp.add_argument("--remote", help="git remote URL override")
    sp.set_defaults(fn=cmd_versions)

    sp = sub.add_parser("use", help="switch to another Unity version (instant in remote mode)")
    sp.add_argument("ref", help="tag (6000.6.5f1), stream prefix (2022.3), latest, latest-stable, or branch (master)")
    sp.set_defaults(fn=cmd_use)

    sp = sub.add_parser("search", help="search the source tree (local clone or on-demand fetch)")
    sp.add_argument("pattern")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--def", dest="mode", action="store_const", const="def",
                   help="type/namespace declarations (class Foo, struct, enum, ...)")
    g.add_argument("--symbol", dest="mode", action="store_const", const="symbol",
                   help="member declarations (methods, properties, fields)")
    g.add_argument("--regex", dest="mode", action="store_const", const="regex",
                   help="treat PATTERN as a raw regular expression")
    g.add_argument("--text", dest="mode", action="store_const", const="text",
                   help="literal substring search (default)")
    sp.add_argument("--path", help="glob filter, e.g. 'Modules/UI/**' or '*.bindings.cs' (no slash = basename)")
    sp.add_argument("--max", type=int, default=100, help="max matches shown (default 100)")
    sp.add_argument("--max-files", type=int, default=60,
                    help="remote mode only: max files fetched per query (default 60)")
    sp.add_argument("-i", "--ignore-case", action="store_true")
    sp.set_defaults(fn=cmd_search, mode="text", ignore_case=False)

    sp = sub.add_parser("member", help="locate a member declaration inside a type's file(s)")
    sp.add_argument("type", help="enclosing type, e.g. Transform")
    sp.add_argument("member", help="member name, e.g. position")
    sp.add_argument("--max", type=int, default=60)
    sp.add_argument("-i", "--ignore-case", action="store_true")
    sp.set_defaults(fn=cmd_member, ignore_case=False)

    sp = sub.add_parser("outline", help="structural outline of a C# file or type")
    sp.add_argument("target", help="repo-relative file path or a type name (e.g. Transform)")
    sp.add_argument("--filter", help="only members whose name matches this regex")
    sp.add_argument("--types-only", action="store_true")
    sp.add_argument("--max", type=int, default=300)
    sp.set_defaults(fn=cmd_outline, types_only=False)

    sp = sub.add_parser("show", help="print a source file with line numbers (fetched on demand in remote mode)")
    sp.add_argument("path", help="repo-relative file path, e.g. Modules/UI/ScriptBindings/UICanvas.bindings.cs")
    sp.add_argument("--from", dest="start", type=int, default=1, help="first line (1-based)")
    sp.add_argument("--to", dest="end", type=int, default=0, help="last line (0 = end of file)")
    sp.add_argument("--max", type=int, default=3000, help="max lines per call (default 3000)")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("tree", help="directory tree with per-folder .cs counts")
    sp.add_argument("subpath", nargs="?", default="")
    sp.add_argument("--depth", type=int, default=2)
    sp.add_argument("--files", action="store_true", help="also list files at the deepest level")
    sp.add_argument("--max", type=int, default=400)
    sp.set_defaults(fn=cmd_tree, files=False)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
