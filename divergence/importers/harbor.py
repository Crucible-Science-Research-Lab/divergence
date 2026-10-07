# divergence/importers/harbor.py
"""
Importer: Harbor Terminal-Bench 2.0 trials with a structured trajectory (ATIF)
-> divergence runs (OTLP-JSON, one file per run) + a DuckDB index + a scored report.

Run from the repository root, after experiments/002-terminal-bench/fetch.py:
    python divergence/importers/harbor.py experiments/002-terminal-bench --axis model --clean
    python divergence/importers/harbor.py experiments/002-terminal-bench --axis harness --clean

Reads   experiments/<note>/raw/submissions/...   result.json + agent/trajectory.json per trial
        experiments/<note>/inputs.json           the submissions on each axis, the tasks
                                                 Terminal-Bench 2.1 revised, and the
                                                 pre-registration file with its registered hash
        experiments/<note>/manifest.tsv          first line: the dataset and its pinned revision
        the pre-registration file                hashed; the import stops unless the hash is
                                                 the registered one
Writes  experiments/<note>-models/ or -harnesses/   runs/<task>/<run_id>.json, experiment.yaml,
                                                 manifest.json: what divergence/analyse.py reads
        experiments/<note>/out/harbor.duckdb     tables trials_<tag>, calls_<tag>
        experiments/<note>/out/harbor_import_<tag>.txt   the report (also printed)
        (<tag> = confirmation on the harness axis, models_confirmation on the model axis)
Everything written is generated and ignored by git; the note's reports/ folder holds the
committed copies of the two reports.

Scope: harnesses that write a structured trajectory only.
A trial with no step log stays in the outcome counts (trials table) but gets no run file.

Step key: one step per TOOL CALL (not per turn), labelled with a harness-neutral
action category: inspect, edit, run, install, fetch, delete, done, other.
A shell call that does several things gets the first category present in PRIORITY.
"plan" (todo lists, think) and "control" (waits, Ctrl-C, bare cd) are recorded but
left out of the key. Consecutive repeats are collapsed (inspect,inspect,edit ->
inspect,edit); the uncollapsed path is kept as divergence.trajectory_raw.

Two axes. An "arm" is whatever varies: the harness (--axis harness, model fixed) or
the model (--axis model, harness fixed at Terminus2). Only the confirmation arms are
imported here; the exploration arms the hypotheses were written on are not fetched.

Input size, two ways. (1) Provider counts from the log, used only for an arm that
passes TOKEN_RULE (a full-context count cannot shrink from step to step). (2)
Log-estimated input: for each model call, the characters in the log before it, divided
by 4, summed over the run. Computed the same way for every arm; it assumes the harness
resends the whole history on each call, so it is an estimate and is labelled as one.

Engine note: analyse.py v0.2 keys a cell by (task, gen_ai.request.model). The root
span carries the ARM in gen_ai.request.model (on the harness axis that is the harness
name, a workaround kept for note 002), the real model in divergence.model and the real
harness in gen_ai.agent.name. A cell key of (task, agent, model) is the proper fix.

History: this is scratch/harbor_import.py 0.4.2-scratch (sha256 0cc038a7...), the version
that scored the confirmation trials on 2 Oct 2026, with its paths and inputs changed for
the public repository. The parsing, the step key and the scoring (section 9) are unchanged.
"""
import argparse
import hashlib
import json
import math
import re
import shlex
import shutil
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]         # the repository root
BASE = "submissions/terminal-bench/2.0"
IMPORTER_VERSION = "0.5.0"
GROUP = "confirmation"             # the only group imported in the public path
SOURCE = "harbor-terminal-bench-2.0"
RESULT_MAX_CHARS = 4000
MSG_MAX_CHARS = 2000
TOKEN_RULE = 0.95                  # provider input counts are used only if the prompt does
                                   # not shrink in at least this share of step pairs
MIN_LOGGED = 3                     # logged trials a (task, harness) cell needs for path measures

REFERENCE_ARMS = ("GPT-5.3-Codex", "Claude-Opus-4.6", "Terminus2")   # paired against the rest
# Thresholds as written in the pre-registration file (2 Oct 2026). Do not edit after
# confirmation data has been opened; the manifest records this file's hash.
THRESHOLDS = {
    "M1_max_hold": 0.60,          # cheaper model holds < 60% of the tasks the reference always passes
    "M2_min_some": 0.50,          # of the tasks not held, >= 50% are 'some', not 'never'
    "M3_ratio": 1.5,              # stopped-but-failed rate >= 1.5x the reference ...
    "M3_points": 0.05,            # ... and >= 5 points higher
    "M4_ratio": 2.0,              # log-estimated input per pass >= 2x the reference
    "M5_spread": 2.0,             # same-outcome task: costliest / cheapest run, log-estimated input
    "M5_min_share": 0.40,         # ... at 2x+ in >= 40% of same-outcome tasks, every model
    "model_arms_share": 0.75,     # M1-M4 supported if they hold for >= 3 of 4 cheaper models
    "H1_min_flip": 0.10,          # >= 10% of tasks flip
    "H2_min_stopfail": 0.08,      # >= 8% of runs that stop on their own fail
    "H3_ratio": 1.5,              # highest / lowest across harnesses, for both rates
    "H3_zero_floor": 0.05,        # if the lowest rate is 0: holds when the highest is >= 5%
    "harness_arms_share": 0.80,   # H1, H2 supported if they hold in >= 4 of 5 harnesses
}

PRIORITY = ["done", "install", "fetch", "run", "edit", "delete", "inspect", "other",
            "control", "plan"]
EXCLUDED_FROM_KEY = {"plan", "control"}

# ── tool names (lower-cased) ─────────────────────────────────────────────────
DONE_TOOLS = {"mark_task_complete", "task_complete", "finish", "end_execution",
              "finish_verification", "submit", "complete_task", "attempt_completion", "done"}
PLAN_TOOLS = {"todowrite", "todoread", "update_plan", "task_tracker", "think", "save_plan",
              "plan", "write_todos", "todo_write", "research_technique"}
CHECK_TOOLS = {"validate_file", "validate_workspace", "check_constraints"}   # counted as run/test
EDIT_TOOLS = {"edit", "write", "multiedit", "write_file", "edit_file", "replace", "apply_patch",
              "create_file", "notebookedit", "str_replace", "insert", "create", "file_write",
              "patch", "file_edit", "replace_file"}
INSPECT_TOOLS = {"read", "read_file", "glob", "grep", "ls", "list_directory", "view_image",
                 "image_read", "analyze_workspace", "search", "find", "list_files",
                 "file_read", "read_media", "view", "bashoutput", "list_dir", "read_many_files",
                 "open_image", "taskoutput"}
FETCH_TOOLS = {"webfetch", "websearch", "google_web_search", "web_search", "fetch", "web_fetch",
               "fetch_url"}
CONTROL_TOOLS = {"killshell", "write_stdin", "wait", "wait_shell_command",
                 "kill_shell_command", "taskstop"}
SHELL_TOOLS = {"bash_command", "bash", "exec", "shell", "terminal", "execute_bash",
               "run_shell_command", "execute", "run_command", "interact_with_shell",
               "exec_command", "shell_command", "run_terminal_cmd", "local_shell",
               "execute_command"}
CMD_KEYS = ("keystrokes", "command", "cmd", "script", "code", "input")

# ── shell programs ───────────────────────────────────────────────────────────
SHELLS = {"bash", "sh", "zsh", "dash"}
WRAPPERS = {"sudo", "time", "nohup", "env", "command", "exec", "stdbuf", "nice", "unbuffer",
            "timeout", "then", "do", "else", "if", "while", "until", "elif", "!", "{", "xargs",
            "eval", "runuser", "sshpass", "su"}
LOOP_HEADS = {"for", "case", "select", "function", "done", "fi", "esac", "}", "]]", "[["}
NOOPS = {"cd", "export", "set", "source", ".", "pushd", "popd", "unset", "alias", "shopt",
         "ulimit", "umask", "local", "declare", "read", "trap", "return", "break", "continue",
         ":", "true", "false", "disown"}
CONTROL_PROGS = {"sleep", "wait", "clear", "exit", "reset", "tmux", "fg", "bg", "kill", "pkill",
                 "killall"}
PKG = {"pip", "pip3", "uv", "poetry", "npm", "yarn", "pnpm", "cargo", "go", "gem", "conda",
       "mamba", "apt", "apt-get", "apt-cache", "apk", "dnf", "yum", "brew", "pipx", "dpkg",
       "opam", "cabal", "luarocks", "cpan", "rustup"}
PKG_QUERY_ONLY = {"pip", "pip3", "conda", "mamba", "apt", "apt-get", "apt-cache", "dpkg", "brew",
                  "gem", "pipx", "apk", "dnf", "yum", "opam", "rustup"}
INSTALL_VERBS = {"install", "add", "sync", "ci", "i", "update", "upgrade", "uninstall", "remove",
                 "get", "create", "download", "-i"}
FETCH_PROGS = {"curl", "wget", "aria2c", "gdown", "scp", "yt-dlp"}
DELETE_PROGS = {"rm", "rmdir", "unlink", "shred"}
EDIT_PROGS = {"tee", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "patch", "applypatch",
              "apply_patch", "install", "truncate", "dd", "zip", "gzip", "xz", "bzip2", "vim", "vi",
              "nano", "ed"}
UNPACK = {"unzip", "gunzip", "bunzip2", "unxz", "unrar", "uncompress"}
INSPECT_PROGS = {"ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg",
                 "wc", "file", "stat", "du", "df", "pwd", "which", "whereis", "type", "echo",
                 "printf", "env", "printenv", "id", "whoami", "uname", "ps", "top", "free",
                 "lscpu", "nproc", "date", "tree", "diff", "cmp", "md5sum", "sha256sum", "sha1sum",
                 "xxd", "hexdump", "od", "strings", "objdump", "readelf", "nm", "ldd", "sort",
                 "uniq", "cut", "tr", "jq", "test", "[", "nvidia-smi", "lsof", "netstat", "ss",
                 "ip", "ifconfig", "hostname", "history", "basename", "dirname", "realpath",
                 "readlink", "awk", "gawk", "column", "nl", "tac", "rev", "seq", "expr", "bc",
                 "locate", "man", "lsb_release", "getconf", "pgrep", "lsblk", "mount", "ag",
                 "fd", "zcat", "zgrep", "pdftotext", "identify", "ffprobe", "exiftool",
                 "llvm-objdump", "mtype", "mdir", "hd"}
RUN_PROGS = {"python", "python3", "node", "make", "cmake", "gcc", "g++", "cc", "c++", "clang",
             "clang++", "rustc", "javac", "java", "rscript", "r", "pytest", "ruby", "julia", "php",
             "lua", "ocaml", "ocamlfind", "ocamlopt", "dune", "docker", "gdb", "sqlite3", "psql",
             "mysql", "ninja", "mvn", "gradle", "tsc", "deno", "bun", "dotnet", "ghc", "swipl",
             "coqc", "lean", "octave", "nasm", "ld", "as", "ar", "strip", "objcopy", "valgrind",
             "strace", "ltrace", "perf", "nginx", "service", "systemctl", "ffmpeg", "convert",
             "openssl", "ssh", "ssh-keygen", "nc", "telnet", "stan", "latex", "pdflatex",
             "xelatex", "tox", "nox", "jupyter", "ipython", "mips-linux-gnu-gcc", "wine"}
GIT_INSPECT = {"status", "log", "diff", "show", "branch", "ls-files", "rev-parse", "remote",
               "blame", "grep", "describe", "reflog", "ls-tree", "cat-file", "shortlog", "tag",
               "fsck", "count-objects", "rev-list", "config", "ls-remote", "whatchanged",
               "name-rev", "for-each-ref", "show-ref", "version", "help"}
GIT_FETCH = {"clone", "fetch", "pull", "submodule"}
VERSION_FLAGS = {"--version", "-V", "-v", "--help", "-h", "version"}

ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
CTRL_KEYS = re.compile(r"^(C-[a-z]|\^[A-Z]|q|exit|clear)$")
DONE_MARK = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
SHELL_WRAP = re.compile(r"^(?:/usr)?(?:/bin/)?(?:bash|sh|zsh)\s+-(?:l|i|li)?c\s+(['\"])(.*)\1\s*$", re.S)
PY_WRITE = re.compile(r"write_text\(|\.write\(|open\([^)]*['\"][wa]b?\+?['\"]|\.writelines\(")
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}

lines_out = []


def say(s=""):
    print(s)
    lines_out.append(s)


def section(title):
    say()
    say(f"== {title} " + "=" * max(0, 70 - len(title)))


# ── classifying one shell command ────────────────────────────────────────────

def strip_heredocs(text):
    """Remove heredoc bodies so their lines are not read as commands. Returns
    (text without bodies, the bodies joined)."""
    src, out, bodies, i = text.split("\n"), [], [], 0
    while i < len(src):
        out.append(src[i])
        m = HEREDOC.search(src[i])
        if m:
            term, body = m.group(2), []
            i += 1
            while i < len(src) and src[i].strip().rstrip("'\")") != term:
                body.append(src[i])
                i += 1
            bodies.append("\n".join(body))
        i += 1
    return "\n".join(out), "\n".join(bodies)


def lex(text):
    lx = shlex.shlex(text, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    lx.commenters = ""
    return list(lx)


def split_segments(tokens):
    seg = []
    for t in tokens:
        if t in OPERATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def git_sub(rest):
    i = 0
    while i < len(rest):
        if rest[i] in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
        elif rest[i].startswith("-"):
            i += 1
        else:
            return rest[i]
    return ""


def classify_segment(seg, bodies, depth):
    """One simple command -> (set of categories, program name, defaulted to run?)."""
    writes = False
    words, skip = [], False
    for i, t in enumerate(seg):
        if skip:
            skip = False
            continue
        if t and all(ch in "<>&" for ch in t):
            if t in (">", ">>") and i + 1 < len(seg):
                tgt = seg[i + 1]
                if tgt != "/dev/null" and not tgt.startswith("&") and not (i and seg[i - 1] == "2"):
                    writes = True
            skip = True
            continue
        words.append(t)

    while words:
        w = words[0]
        if w == "command" and words[1:2] in (["-v"], ["-V"]):
            return {"inspect"}, "command -v", False
        if ASSIGN.match(w) or w in WRAPPERS:
            words = words[1:]
            if w in ("timeout", "nice", "stdbuf", "env", "sudo", "xargs", "runuser", "sshpass", "su"):
                while words and (words[0].startswith("-") or DURATION.match(words[0])
                                 or ASSIGN.match(words[0])):
                    takes_arg = words[0] in ("-u", "-p", "-g", "-l", "-s", "-k", "-n", "-I")
                    words = words[2:] if takes_arg else words[1:]
            continue
        break
    if not words or words[0] in LOOP_HEADS:
        return set(), None, False
    if (words[0].startswith(("-", "((")) or words[0].isdigit() or not words[0].strip()):
        return set(), None, False            # leftovers of a split, not a command
    prog, rest = words[0].split("/")[-1], words[1:]
    low = prog.lower()
    if low in NOOPS:
        return set(), None, False
    if DONE_MARK in " ".join(words):
        return {"done"}, "<submit>", False
    if low in SHELLS:
        for i, t in enumerate(rest):
            if t in ("-c", "-lc", "-ic", "-lic", "-cl") and i + 1 < len(rest) and depth < 3:
                cats, progs, dflt = categories(rest[i + 1], depth + 1)
                return cats, (dflt[0] if dflt else (progs[0] if progs else prog)), bool(dflt)
        return {"run"}, prog, False
    if low in CONTROL_PROGS:
        return {"control"}, prog, False
    if rest and len(rest) <= 2 and rest[0] in VERSION_FLAGS:
        return {"inspect"}, prog, False

    if low.startswith("python") or low in ("pypy", "pypy3"):
        if "-m" in rest and rest.index("-m") + 1 < len(rest):
            mod = rest[rest.index("-m") + 1]
            if mod == "pip":
                low, prog, rest = "pip", "pip", rest[rest.index("-m") + 2:]
            elif mod in ("venv", "ensurepip", "virtualenv"):
                return {"install"}, prog, False
            else:
                return {"run"}, prog, False
        else:
            code = ""
            if "-c" in rest and rest.index("-c") + 1 < len(rest):
                code = rest[rest.index("-c") + 1]
            elif not rest or rest[0] == "-":
                code = bodies
            return ({"edit"} if PY_WRITE.search(code) else {"run"}), prog, False

    if low in PKG:
        verbs = [r for r in rest[:4] if not r.startswith("-") or r == "-i"]
        if any(v in INSTALL_VERBS for v in verbs):
            return {"install"}, prog, False
        return ({"inspect"} if low in PKG_QUERY_ONLY else {"run"}), prog, False
    if low == "git":
        sub = git_sub(rest)
        if sub in GIT_FETCH:
            return {"fetch"}, "git " + sub, False
        if sub == "clean":
            return {"delete"}, "git clean", False
        if sub in GIT_INSPECT:
            return {"inspect"}, "git " + sub, False
        return {"edit"}, "git " + sub, False
    if low in FETCH_PROGS or (low in ("huggingface-cli", "hf") and "download" in rest):
        return {"fetch"}, prog, False
    if low in DELETE_PROGS:
        return {"delete"}, prog, False
    if low == "find":
        if "-delete" in rest or ("-exec" in rest and "rm" in rest):
            return {"delete"}, prog, False
        return ({"edit"} if writes else {"inspect"}), prog, False
    if low == "sed":
        inplace = any(t.startswith("--in-place") or
                      (t.startswith("-") and not t.startswith("--") and "i" in t[1:])
                      for t in rest[:3])
        return ({"edit"} if inplace or writes else {"inspect"}), prog, False
    if low == "perl":
        inplace = any(t.startswith("-") and not t.startswith("--") and "i" in t[1:]
                      for t in rest[:3])
        return ({"edit"} if inplace else {"run"}), prog, False
    if low in UNPACK:
        return ({"inspect"} if "-l" in rest else {"edit"}), prog, False
    if low == "tar":
        first = rest[0].lstrip("-") if rest else ""
        listing = ("t" in first and "x" not in first and "c" not in first) or "--list" in rest
        return ({"inspect"} if listing else {"edit"}), prog, False
    if low in ("7z", "7za", "7zr"):
        return ({"inspect"} if rest[:1] in (["l"], ["t"]) else {"edit"}), prog, False
    if low in EDIT_PROGS:
        return {"edit"}, prog, False
    if low in INSPECT_PROGS:
        return ({"edit"} if writes else {"inspect"}), prog, False
    known = (low in RUN_PROGS or "/" in words[0] or low.endswith((".sh", ".py"))
             or low.startswith(("qemu", "$")))
    return {"run"}, prog, not known


def categories(text, depth=0):
    """A shell command string -> (set of categories, programs, programs that defaulted to run)."""
    text = str(text).replace("\r", "")
    stripped = text.strip()
    if not stripped or CTRL_KEYS.match(stripped):
        return {"control"}, ([stripped] if stripped else []), []
    m = SHELL_WRAP.match(stripped)
    if m and depth < 3:
        q, inner = m.group(1), m.group(2)
        if q == "'":
            inner2 = inner.replace("'\\''", "\x00").replace("'\"'\"'", "\x00")
            if "'" not in inner2:
                return categories(inner2.replace("\x00", "'"), depth + 1)
        else:
            return categories(inner.replace('\\"', '"'), depth + 1)
    body_free, bodies = strip_heredocs(text)
    kept = [ln for ln in body_free.split("\n") if not ln.lstrip().startswith("#")]
    joined = " ; ".join(kept)
    try:
        tokens = lex(joined)
    except ValueError:                       # unbalanced quotes: fall back to a plain split
        tokens = [t.strip("'\"") for t in re.sub(r"(&&|\|\||;|\|)", r" \1 ", joined).split()]
        tokens = [t for t in tokens if t]
    cats, progs, defaulted = set(), [], []
    for seg in split_segments(tokens):
        c, p, d = classify_segment(seg, bodies, depth)
        cats |= c
        if p:
            progs.append(p)
            if d:
                defaulted.append(p)
    return (cats or {"control"}), progs, defaulted


def primary(cats):
    return next((c for c in PRIORITY if c in cats), "other")


# ── classifying one tool call ────────────────────────────────────────────────

def as_args(args):
    if isinstance(args, str):
        try:
            return json.loads(args)
        except Exception:
            return {"_raw": args}
    return args if args is not None else {}


def command_text(args):
    def from_list(v):
        v = [str(x) for x in v]
        if len(v) >= 3 and v[0].split("/")[-1] in SHELLS and v[1] in ("-lc", "-c", "-ic"):
            return v[2]
        return " ".join(v)
    if isinstance(args, list):
        return from_list(args)
    if not isinstance(args, dict):
        return str(args or "")
    for k in CMD_KEYS:
        v = args.get(k)
        if v not in (None, ""):
            return from_list(v) if isinstance(v, list) else str(v)
    return None


def classify_call(fn, args):
    name = str(fn or "").lower()
    a = args if isinstance(args, dict) else {}
    out = {"category": "other", "cats": [], "programs": [], "defaulted": [], "command": ""}
    if name in DONE_TOOLS:
        out["category"] = "done"
    elif name in PLAN_TOOLS:
        out["category"] = "plan"
    elif "editor" in name or name in ("str_replace_based_edit_tool", "file_edit"):
        # file-editor tools carry a sub-command (view/create/str_replace), not a shell command
        out["category"] = "inspect" if a.get("command") in ("view", "read") else "edit"
    elif name in EDIT_TOOLS:
        out["category"] = "edit"
    elif name in INSPECT_TOOLS:
        out["category"] = "inspect"
    elif name in FETCH_TOOLS:
        out["category"] = "fetch"
    elif name in CHECK_TOOLS:
        out["category"] = "run"
    elif name in CONTROL_TOOLS:
        out["category"] = "control"
    else:
        cmd = command_text(args)
        if cmd is None and name in SHELL_TOOLS:
            cmd = ""
        if cmd is not None:
            cats, progs, dflt = categories(cmd)
            out.update(category=primary(cats), cats=sorted(cats), programs=progs,
                       defaulted=dflt, command=cmd)
    if not out["cats"]:
        out["cats"] = [out["category"]]
    return out


# ── OTLP-JSON encoding (same shapes as divergence/importers/tau.py) ──────────

def _attr_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_attr_value(x) for x in v]}}
    return {"stringValue": str(v)}


def ATTRS(d):
    return [{"key": k, "value": _attr_value(v)} for k, v in (d or {}).items() if v is not None]


def _id(seed, n_hex):
    return hashlib.sha256(seed.encode()).hexdigest()[:n_hex]


def _ns(ts, fallback):
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000) * 1000
        except ValueError:
            pass
    return fallback


def _span(trace_id, span_id, parent, name, start, end, attrs, events=None):
    s = {"traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
         "startTimeUnixNano": str(start), "endTimeUnixNano": str(max(end, start)),
         "attributes": ATTRS(attrs), "events": events or [], "status": {"code": 0}}
    if parent:
        s["parentSpanId"] = parent
    return s


def _provider(model):
    m = str(model).lower()
    if "claude" in m:
        return "anthropic"
    if "gpt" in m or m.startswith(("o1", "o3", "o4")):
        return "openai"
    if "gemini" in m:
        return "gcp.gemini"
    for needle, name in (("deepseek", "deepseek"), ("glm", "z-ai"), ("kimi", "moonshot"),
                         ("minimax", "minimax")):
        if needle in m:
            return name
    return "unknown"


def text_of(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(text_of(x) for x in v)
    if isinstance(v, dict):
        for k in ("text", "content", "message"):
            if k in v:
                return text_of(v[k])
        return json.dumps(v)
    return str(v)


def text_len(v):
    """Characters in every string inside v."""
    if v is None:
        return 0
    if isinstance(v, str):
        return len(v)
    if isinstance(v, list):
        return sum(text_len(x) for x in v)
    if isinstance(v, dict):
        return sum(text_len(x) for x in v.values())
    return len(str(v))


def find_steps(obj):
    """ATIF steps and final metrics, possibly nested (CodeBrain wraps them)."""
    if isinstance(obj, list):
        return obj, {}
    if isinstance(obj, dict):
        if isinstance(obj.get("steps"), list):
            return obj["steps"], (obj.get("final_metrics") or {})
        for k in ("atif_trajectory", "trajectory"):
            if k in obj:
                steps, fm = find_steps(obj[k])
                if steps is not None:
                    return steps, fm
    return None, {}


# ── one trial -> one run ─────────────────────────────────────────────────────

def read_result(result):
    vr = result.get("verifier_result") or {}
    rewards = vr.get("rewards") if isinstance(vr.get("rewards"), dict) else {}
    reward = rewards.get("reward", vr.get("reward"))
    try:
        reward = float(reward) if reward is not None else None
    except (TypeError, ValueError):
        reward = None
    exc = result.get("exception_info") or None
    exc_type = None
    if isinstance(exc, dict):
        exc_type = exc.get("exception_type") or exc.get("type") or "Exception"
    elif exc:
        exc_type = str(exc)[:60]
    ar = result.get("agent_result") or {}
    ae = result.get("agent_execution") if isinstance(result.get("agent_execution"), dict) else {}
    t0 = ae.get("started_at") or result.get("started_at")
    t1 = ae.get("finished_at") or result.get("finished_at")
    dur = (_ns(t1, 0) - _ns(t0, 0)) / 1e9 if t0 and t1 else None
    return {"duration_s": dur if dur and dur > 0 else None, "reward": reward,
            "verdict": "pass" if reward is not None and reward >= 1.0 else "fail",
            "exception_type": exc_type,
            "termination": exc_type or "completed",
            "in_tok": ar.get("n_input_tokens"), "out_tok": ar.get("n_output_tokens"),
            "cache_tok": ar.get("n_cache_tokens"), "cost_usd": ar.get("cost_usd"),
            "started_at": result.get("started_at"), "finished_at": result.get("finished_at")}


def convert(trial, steps, final_metrics):
    run_id = trial["run_id"]
    trace_id, root_id = _id(run_id, 32), _id(run_id + "/root", 16)
    model, harness, arm = trial["model"], trial["harness"], trial["arm"]
    base = _ns(trial["started_at"], 0)
    last = base
    spans, events, calls, chat_idx = [], [], [], []
    agent_steps = replies = 0
    step_usage = False
    seen_chars = est_chars = 0         # characters in the log so far / summed before each call
    prompts = []                       # provider prompt tokens per model call

    for seq, st in enumerate(steps):
        if not isinstance(st, dict):
            continue
        start = max(_ns(st.get("timestamp"), last + 1_000_000), last + 1) + seq
        last = start
        source = st.get("source") or st.get("role") or "agent"
        message = text_of(st.get("message") if st.get("message") is not None else st.get("content"))
        tcs = [t for t in (st.get("tool_calls") or []) if isinstance(t, dict)]
        size = len(message) + text_len(st.get("tool_calls")) + text_len(st.get("observation"))
        if source in ("user", "system") and not tcs:
            seen_chars += size
            events.append({"timeUnixNano": str(start), "name": f"{source}_message",
                           "attributes": ATTRS({"divergence.turn_idx": seq,
                                                "divergence.content": message[:MSG_MAX_CHARS]})})
            continue
        agent_steps += 1
        if not tcs:
            replies += 1
        m = st.get("metrics") or {}
        it, ot = int(m.get("prompt_tokens") or 0), int(m.get("completion_tokens") or 0)
        step_usage = step_usage or bool(it or ot)
        est_chars += seen_chars
        seen_chars += size
        prompts.append(it)

        results = ((st.get("observation") or {}).get("results")) if isinstance(
            st.get("observation"), dict) else None
        results = [r for r in (results or []) if isinstance(r, dict)]
        by_id = {r.get("source_call_id"): r for r in results if r.get("source_call_id")}

        parts = [{"type": "text", "content": message}] if message else []
        norm = []
        for k, tc in enumerate(tcs):
            fn = tc.get("function_name") or tc.get("tool_name") or tc.get("name") or "?"
            args = as_args(tc.get("arguments") if "arguments" in tc else
                           tc.get("parameters", tc.get("input")))
            cid = tc.get("tool_call_id") or tc.get("id") or f"{seq}.{k}"
            res = by_id.get(cid) or (results[k] if k < len(results) and not by_id else {})
            norm.append((fn, args, cid, text_of(res.get("content"))))
            parts.append({"type": "tool_call", "id": cid, "name": fn, "arguments": args})

        chat_idx.append(len(spans))
        spans.append(_span(trace_id, _id(f"{run_id}/chat/{seq}", 16), root_id, f"chat {model}",
                           start, start + 1_000_000, {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": _provider(model),
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": it,
            "gen_ai.usage.output_tokens": ot,
            "gen_ai.output.messages": json.dumps(
                [{"role": "assistant", "parts": parts,
                  "finish_reason": "tool_call" if tcs else "stop"}]),
            "divergence.cost_usd": float(m["cost_usd"]) if m.get("cost_usd") is not None else None,
            "divergence.cached_tokens": int(m.get("cached_tokens") or 0),
            "divergence.turn_idx": seq,
            "divergence.usage_recorded": bool(m),
        }))
        for k, (fn, args, cid, content) in enumerate(norm):
            c = classify_call(fn, args)
            t0 = start + 1000 * (k + 1)
            spans.append(_span(trace_id, _id(f"{run_id}/tool/{seq}/{k}", 16), root_id,
                               f"execute_tool {fn}", t0, t0 + 1000, {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": fn,
                "gen_ai.tool.call.id": cid,
                "gen_ai.tool.call.arguments": json.dumps(args)[:RESULT_MAX_CHARS],
                "gen_ai.tool.call.result": content[:RESULT_MAX_CHARS],
                "divergence.step_category": c["category"],
                "divergence.step_categories": ",".join(c["cats"]),
                "divergence.result_empty": not content.strip(),
            }))
            calls.append({"run_id": run_id, "arm": arm, "harness": harness, "task": trial["task"],
                          "seq": len(calls), "step": seq, "fn": fn, "category": c["category"],
                          "cats": ",".join(c["cats"]), "programs": ",".join(c["programs"]),
                          "defaulted": ",".join(c["defaulted"]),
                          "command": c["command"][:300], "obs_chars": len(content)})

    raw = [c["category"] for c in calls if c["category"] not in EXCLUDED_FROM_KEY]
    collapsed = [x for i, x in enumerate(raw) if i == 0 or raw[i - 1] != x]

    in_tok = trial["in_tok"] if trial["in_tok"] is not None else final_metrics.get("total_prompt_tokens")
    out_tok = trial["out_tok"] if trial["out_tok"] is not None else final_metrics.get("total_completion_tokens")
    cost = trial["cost_usd"] if trial["cost_usd"] is not None else final_metrics.get("total_cost_usd")
    if not step_usage and chat_idx and (in_tok or out_tok):
        # no per-step usage: put the trial totals on the last chat span so sums are right
        a = {x["key"]: x for x in spans[chat_idx[-1]]["attributes"]}
        a["gen_ai.usage.input_tokens"]["value"] = {"intValue": str(int(in_tok or 0))}
        a["gen_ai.usage.output_tokens"]["value"] = {"intValue": str(int(out_tok or 0))}
        spans[chat_idx[-1]]["attributes"].append(
            {"key": "divergence.usage_from_total", "value": {"boolValue": True}})

    end = max(_ns(trial["finished_at"], last + 1_000_000), last + 1)
    root = _span(trace_id, root_id, None, f"invoke_agent {harness}", base or last, end, {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.provider.name": _provider(model),
        "gen_ai.agent.name": harness,
        "gen_ai.request.model": arm,              # the cell key, see module docstring
        "divergence.model": model,
        "divergence.run_id": run_id,
        "divergence.task_id": trial["task"],
        "divergence.run_index": trial["run_index"],
        "divergence.trajectory": "|".join(collapsed),
        "divergence.trajectory_raw": "|".join(raw),
        "divergence.step_key": "action_category_per_tool_call_collapsed",
        "divergence.termination": trial["termination"],
        "divergence.turns": agent_steps,
        "divergence.answered": trial["exception_type"] is None,
        "divergence.declared_done": any(c["category"] == "done" for c in calls),
        "divergence.verdict": trial["verdict"],
        "divergence.reward": trial["reward"] if trial["reward"] is not None else -1.0,
        "divergence.cost_usd": float(cost) if cost is not None else None,
        "divergence.replies": replies,
        "divergence.tool_calls": len(calls),
        "divergence.task_validity": "unchecked",
        "divergence.source": SOURCE,
        "divergence.source_trial": trial["trial_name"],
        "divergence.source_submission": trial["submission"],
    }, events=events)
    doc = {"resourceSpans": [{
        "resource": {"attributes": ATTRS({"service.name": "divergence",
                                          "divergence.run_id": run_id,
                                          "divergence.source": SOURCE})},
        "scopeSpans": [{"scope": {"name": "crucible.divergence.importers.harbor",
                                  "version": IMPORTER_VERSION},
                        "schemaUrl": "https://opentelemetry.io/schemas/1.44.0",
                        "spans": [root] + spans}]}]}
    last_edit = max((i for i, c in enumerate(raw) if c == "edit"), default=None)
    tested = ("no_edit" if last_edit is None else
              "tested" if "run" in raw[last_edit + 1:] else "untested")
    stats = {"tested": tested, "first_action": raw[0] if raw else "",
             "edit_rounds": collapsed.count("edit"), "n_run": raw.count("run"),
             "n_install": raw.count("install"), "n_fetch": raw.count("fetch"),
             "n_delete": raw.count("delete"),
             "n_steps": agent_steps, "n_calls": len(calls), "key_raw": len(raw),
             "key_collapsed": len(collapsed), "path": "|".join(collapsed),
             "path_raw": "|".join(raw), "in_tok": in_tok, "out_tok": out_tok, "cost_usd": cost,
             "declared_done": any(c["category"] == "done" for c in calls),
             "step_usage": step_usage,
             "est_in_tok": est_chars / 4 if est_chars else None,
             "pairs": sum(x > 0 and y > 0 for x, y in zip(prompts, prompts[1:])),
             "pairs_ok": sum(y >= x > 0 for x, y in zip(prompts, prompts[1:]))}
    return doc, calls, stats


# ── main ─────────────────────────────────────────────────────────────────────

def find_trials(sub_dir):
    """Trial-level result.json files only (job folders also hold a result.json)."""
    out = []
    for p in sorted(sub_dir.rglob("result.json")):
        try:
            r = json.loads(p.read_text(errors="replace"))
        except Exception:
            continue
        if isinstance(r, dict) and r.get("task_name") and r.get("trial_name"):
            out.append((p.parent, r))
    return out


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def num(x):
    return f"{x:,.0f}" if x is not None else "-"


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def rel(p):
    """A path as printed: relative to the repository root when it lies inside it."""
    try:
        return Path(p).resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(p)


def read_inputs(home):
    """inputs.json, plus the dataset and its pinned revision from manifest.tsv's first line."""
    inputs = json.loads((home / "inputs.json").read_text())
    with open(home / "manifest.tsv") as f:
        m = re.match(r"^# inputs of note \S+: (\S+) @ ([0-9a-f]{40})\s*$", f.readline())
    if not m:
        raise SystemExit(f"{rel(home / 'manifest.tsv')}: the first line does not name the "
                         "repository and revision")
    return inputs, m.group(1), m.group(2)


def main():
    ap = argparse.ArgumentParser(description="Import one axis of a Harbor note from its raw/ folder.")
    ap.add_argument("experiment", type=Path,
                    help="the note's folder, e.g. experiments/002-terminal-bench")
    ap.add_argument("--axis", choices=["harness", "model"], required=True,
                    help="harness: same model, different harnesses; "
                         "model: same harness (Terminus2), different models")
    ap.add_argument("--clean", action="store_true", help="delete existing runs/ first")
    args = ap.parse_args()
    home = args.experiment.resolve()
    for name in ("inputs.json", "manifest.tsv"):
        if not (home / name).exists():
            raise SystemExit(f"not found: {rel(home / name)}")
    inputs, repo, revision = read_inputs(home)
    cache, out_dir = home / "raw", home / "out"
    prereg = REPO / inputs["preregistration"]["file"]
    if not prereg.exists():
        raise SystemExit(f"the pre-registration file is missing: {rel(prereg)}")
    prereg_hash = hashlib.sha256(prereg.read_bytes()).hexdigest()
    if prereg_hash != inputs["preregistration"]["sha256"]:
        raise SystemExit(f"{rel(prereg)} is not the registered file: sha256 {prereg_hash}, "
                         f"registered {inputs['preregistration']['sha256']}")
    axis = inputs["axes"][args.axis]
    fixed = axis["held_fixed"]                           # the harness or the model held fixed
    subs = list(axis["submissions"])
    tag = GROUP if args.axis == "harness" else f"models_{GROUP}"
    revised = set(inputs["tasks_revised_in_tb21"]["tasks"])
    exp = home.parent / f"{home.name}-{axis['folder']}"  # generated: runs/, experiment.yaml
    runs_dir = exp / "runs"
    if args.clean and runs_dir.exists():
        shutil.rmtree(runs_dir)

    say(f"Harbor import ({args.axis} axis, {GROUP}; held fixed: {fixed}), "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    say(f"repo {repo}  revision {revision}")
    say(f"submissions: {', '.join(subs)}")
    say(f"importer {IMPORTER_VERSION}; pre-registration sha256: {prereg_hash}")
    say(f"tasks revised in Terminal-Bench 2.1: {len(revised)}")

    trials_rows, calls_rows, labels = [], [], []
    parse_errors, empty_logs = Counter(), Counter()
    for sub in subs:
        harness, _, sub_model = sub.partition("__")
        arm = harness if args.axis == "harness" else sub_model
        model = fixed if args.axis == "harness" else sub_model
        labels.append(arm)
        found = find_trials(cache / BASE / sub) if (cache / BASE / sub).exists() else []
        if not found:
            say(f"!! no trials on disk for {sub} (run fetch.py first)")
        by_task = defaultdict(list)
        for tdir, r in found:
            by_task[r["task_name"]].append((tdir, r))
        for task, items in sorted(by_task.items()):
            items.sort(key=lambda x: (str(x[1].get("started_at") or ""), x[1]["trial_name"]))
            for index, (tdir, r) in enumerate(items):
                trial = {"submission": sub, "arm": arm, "harness": harness, "model": model,
                         "task": task, "revised": task in revised,
                         "trial_name": r["trial_name"], "run_index": index,
                         "run_id": f"{task}__{arm}__{index:03d}", **read_result(r)}
                tpath = tdir / "agent" / "trajectory.json"
                steps, fm = None, {}
                if tpath.exists():
                    try:
                        steps, fm = find_steps(json.loads(tpath.read_text(errors="replace")))
                    except Exception:
                        parse_errors[arm] += 1
                trial["has_log"] = False
                if steps:
                    doc, calls, stats = convert(trial, steps, fm)
                    if not stats["n_steps"]:
                        empty_logs[arm] += 1
                if steps and stats["n_steps"]:
                    trial["has_log"] = True
                    out = runs_dir / task / f"{trial['run_id']}.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(doc))
                    calls_rows += calls
                    trial.update(stats)
                trials_rows.append(trial)

    if not trials_rows:
        raise SystemExit(f"No trials found under {rel(cache / BASE)}; run fetch.py first.")

    # experiment.yaml + manifest.json
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "experiment.yaml").write_text(
        f"slug: {exp.name}\nstatus: {GROUP}\n"
        f"axis: {args.axis}\nheld_fixed: {fixed}\n"
        "question: >\n" + (
            "  Same model, different harnesses: when the grader says pass, what did the\n"
            "  agent do to get there, and does it do it the same way every time?\n"
            if args.axis == "harness" else
            "  Same harness, different models: which tasks still hold when the model\n"
            "  changes, and what does one passed task cost in tokens on each?\n") +
        f"models:\n  {GROUP}: [{', '.join(labels)}]\n"
        "provider: harbor-leaderboard-submissions\ntier: none\n"
        "limitations:\n" + (
            "  - The 'model' column is the harness; the model is fixed.\n"
            if args.axis == "harness" else
            "  - Each model is as its submitter configured and served it; provider,\n"
            "    reasoning effort, temperature and parser differ.\n") +
        "  - Step key is an action category per tool call, consecutive repeats collapsed.\n"
        "  - Trials with no step log are counted in outcomes but have no run file.\n")
    (exp / "manifest.json").write_text(json.dumps({
        "importer": f"harbor {IMPORTER_VERSION}",
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"repo": repo, "revision": revision, "licence": "Apache-2.0",
                   "file_hashes": rel(home / "manifest.tsv")},
        "axis": args.axis, "group": GROUP, "held_fixed": fixed, "arms": labels,
        "submissions": subs, "tasks_revised_in_tb21": len(revised),
        "step_key": {"unit": "tool call", "priority": PRIORITY,
                     "excluded": sorted(EXCLUDED_FROM_KEY), "collapsed": True},
        "preregistration": {"file": rel(prereg), "sha256": prereg_hash},
        "importer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "thresholds": THRESHOLDS,
        "token_rule": {"min_share_of_step_pairs_where_prompt_does_not_shrink": TOKEN_RULE},
        "trials": len(trials_rows), "runs": sum(t["has_log"] for t in trials_rows),
    }, indent=1))

    # ── report ───────────────────────────────────────────────────────────────
    by_h = defaultdict(list)
    for t in trials_rows:
        by_h[t["arm"]].append(t)

    section("1. Trials and outcomes per arm")
    say("pass all = over every trial (leaderboard view); pass logged = over trials with a step log")
    say(f"logs with no agent activity (counted as no log): {dict(empty_logs) or 'none'}")
    say(f"{'arm':<16} {'trials':>6} {'logged':>6} {'no log':>6} {'bad json':>8} "
        f"{'pass all':>8} {'pass logged':>11} {'pass no-log':>11}")
    for h in labels:
        ts = by_h[h]
        lg = [t for t in ts if t["has_log"]]
        nl = [t for t in ts if not t["has_log"]]
        say(f"{h:<16} {len(ts):>6} {len(lg):>6} {len(nl):>6} {parse_errors[h]:>8} "
            f"{pct(sum(t['verdict'] == 'pass' for t in ts), len(ts)):>8} "
            f"{pct(sum(t['verdict'] == 'pass' for t in lg), len(lg)):>11} "
            f"{pct(sum(t['verdict'] == 'pass' for t in nl), len(nl)):>11}")

    section("2. How runs ended (all trials)")
    say("stopped itself = no exception recorded; done call = an explicit 'declare done' tool call")
    for h in labels:
        ts = by_h[h]
        term = Counter(t["termination"] for t in ts)
        say(f"-- {h}: " + ", ".join(f"{k} {v}" for k, v in term.most_common(5)))
        lg = [t for t in ts if t["has_log"]]
        fails = [t for t in ts if t["verdict"] == "fail"]
        say(f"   failed trials {len(fails)}: stopped itself "
            f"{sum(t['exception_type'] is None for t in fails)}, exception "
            f"{sum(t['exception_type'] is not None for t in fails)}; "
            f"logged runs with a done call {sum(bool(t.get('declared_done')) for t in lg)}/{len(lg)}"
            f", of which failed {sum(bool(t.get('declared_done')) and t['verdict'] == 'fail' for t in lg)}")
        say("   no-log trials by ending: " + (", ".join(
            f"{k} {v}" for k, v in Counter(t["termination"] for t in ts
                                           if not t["has_log"]).most_common(4)) or "none"))

    section("3. Size of a run (logged trials; median)")
    say(f"{'arm':<16} {'turns':>6} {'calls':>6} {'key raw':>8} {'key collapsed':>13} "
        f"{'in tok':>10} {'out tok':>8} {'cost $':>7} {'step usage':>10}")
    for h in labels:
        lg = [t for t in by_h[h] if t["has_log"]]
        cost = med([t.get("cost_usd") for t in lg])
        say(f"{h:<16} {med([t['n_steps'] for t in lg]) or 0:>6g} "
            f"{med([t['n_calls'] for t in lg]) or 0:>6g} "
            f"{med([t['key_raw'] for t in lg]) or 0:>8g} "
            f"{med([t['key_collapsed'] for t in lg]) or 0:>13g} "
            f"{num(med([t.get('in_tok') for t in lg])):>10} "
            f"{num(med([t.get('out_tok') for t in lg])):>8} "
            f"{(f'{cost:.2f}' if cost is not None else '-'):>7} "
            f"{pct(sum(bool(t.get('step_usage')) for t in lg), len(lg)):>10}")

    section("4. Action mix (share of tool calls)")
    cats_seen = [c for c in PRIORITY]
    say(f"{'arm':<16} " + " ".join(f"{c:>8}" for c in cats_seen))
    by_hc = defaultdict(Counter)
    tools = defaultdict(Counter)
    for c in calls_rows:
        by_hc[c["arm"]][c["category"]] += 1
        tools[c["arm"]][c["fn"]] += 1
    for h in labels:
        n = sum(by_hc[h].values())
        say(f"{h:<16} " + " ".join(f"{pct(by_hc[h][c], n):>8}" for c in cats_seen))
    say()
    for h in labels:
        say(f"   {h} tools: " + ", ".join(f"{k} {v:,}" for k, v in tools[h].most_common(8)))

    section(f"5. Cells (task x arm) with >= {MIN_LOGGED} logged runs")
    say(f"{'arm':<16} {'cells':>5} {'flip':>5} {'all pass':>8} {'all fail':>8} "
        f"{'paths/cell':>10} {'raw paths':>9} {'all distinct':>12}")
    for h in labels:
        cells = defaultdict(list)
        for t in by_h[h]:
            if t["has_log"]:
                cells[t["task"]].append(t)
        cells = {k: v for k, v in cells.items() if len(v) >= MIN_LOGGED}
        flips = sum(len({t["verdict"] for t in v}) == 2 for v in cells.values())
        allp = sum(all(t["verdict"] == "pass" for t in v) for v in cells.values())
        dp = [len({t["path"] for t in v}) for v in cells.values()]
        dr = [len({t["path_raw"] for t in v}) for v in cells.values()]
        alld = sum(len({t["path"] for t in v}) == len(v) for v in cells.values())
        say(f"{h:<16} {len(cells):>5} {flips:>5} {allp:>8} {len(cells) - flips - allp:>8} "
            f"{med(dp) or 0:>10g} {med(dr) or 0:>9g} {alld:>12}")

    section("6. To tune the step key")
    dflt, other = Counter(), Counter()
    for c in calls_rows:
        for p in filter(None, c["defaulted"].split(",")):
            dflt[p] += 1
        if c["category"] == "other":
            other[c["fn"]] += 1
    say("programs that fell through to 'run' (not in any list), top 40:")
    say("  " + ", ".join(f"{k} {v:,}" for k, v in dflt.most_common(40)))
    say("share of tool calls with a program that fell through, per arm: " + ", ".join(
        f"{h} {pct(sum(bool(c['defaulted']) for c in calls_rows if c['arm'] == h), sum(by_hc[h].values()))}"
        for h in labels))
    say("tools labelled 'other':")
    say("  " + (", ".join(f"{k} {v:,}" for k, v in other.most_common(20)) or "none"))
    say("most common collapsed paths (all arms):")
    for p, n in Counter(t["path"] for t in trials_rows if t["has_log"]).most_common(8):
        say(f"  {n:>4}  {p[:110]}")

    def frac(a, b):
        return f"{a}/{b} {pct(a, b)}" if b else "-"

    def failed(ts):
        return sum(t["verdict"] == "fail" for t in ts)

    def grade(ts):
        p = len(ts) - failed(ts)
        return "every" if p == len(ts) else "never" if p == 0 else "some"

    def measure_rows(keep):
        rows = defaultdict(dict)
        for h in labels:
            ts = [t for t in by_h[h] if keep(t)]
            lg = [t for t in ts if t["has_log"]]
            cells = defaultdict(list)
            for t in lg:
                cells[t["task"]].append(t)
            cells = {k: v for k, v in cells.items() if len(v) >= MIN_LOGGED}
            stopped = [t for t in ts if t["exception_type"] is None]
            ls = [t for t in lg if t["exception_type"] is None]      # logged and stopped itself
            tested = [t for t in ls if t["tested"] == "tested"]
            untested = [t for t in ls if t["tested"] == "untested"]
            noedit = [t for t in ls if t["tested"] == "no_edit"]
            blind = [t for t in lg if t["first_action"] not in ("inspect", "")]
            looked = [t for t in lg if t["first_action"] == "inspect"]
            pairs = sum(t.get("pairs") or 0 for t in lg)
            share = sum(t.get("pairs_ok") or 0 for t in lg) / pairs if pairs else None
            flag = "" if share is not None and share >= TOKEN_RULE else " !"
            rows["passed (all trials)"][h] = frac(len(ts) - failed(ts), len(ts))
            rows["tasks whose outcome flips"][h] = frac(
                sum(len({t["verdict"] for t in v}) == 2 for v in cells.values()), len(cells))
            rows["timed out (all trials)"][h] = frac(
                sum(t["exception_type"] == "AgentTimeoutError" for t in ts), len(ts))
            rows["stopped by itself, but failed"][h] = frac(failed(stopped), len(stopped))
            rows["explicit done call, but failed"][h] = frac(
                sum(bool(t["declared_done"]) and t["verdict"] == "fail" for t in lg),
                sum(bool(t["declared_done"]) for t in lg))
            rows["stopped runs: tested after last edit"][h] = frac(len(tested), len(ls))
            rows["stopped runs: edited, never re-ran"][h] = frac(len(untested), len(ls))
            rows["stopped runs: no edit step at all"][h] = frac(len(noedit), len(ls))
            rows["  fail rate if tested"][h] = frac(failed(tested), len(tested))
            rows["  fail rate if untested"][h] = frac(failed(untested), len(untested))
            rows["  fail rate if no edit"][h] = frac(failed(noedit), len(noedit))
            rows["cells where runs disagree on testing"][h] = frac(
                sum(len({t["tested"] for t in v if t["tested"] != "no_edit"}) == 2
                    for v in cells.values()), len(cells))
            rows["first action is not a look"][h] = frac(len(blind), len(lg))
            rows["  fail rate if looked first"][h] = frac(failed(looked), len(looked))
            rows["  fail rate if not"][h] = frac(failed(blind), len(blind))
            rows["runs that fetched from the network"][h] = frac(
                sum(t["n_fetch"] > 0 for t in lg), len(lg))
            rows["runs that installed packages"][h] = frac(
                sum(t["n_install"] > 0 for t in lg), len(lg))
            rows["same path, different outcome (cells)"][h] = frac(
                sum(any(len({t["verdict"] for t in v if t["path"] == p}) == 2
                        for p in {t["path"] for t in v}) for v in cells.values()), len(cells))

            def spread(key, same_outcome):
                out = []
                for v in cells.values():
                    if same_outcome and len({t["verdict"] for t in v}) != 1:
                        continue
                    xs = [t.get(key) for t in v]
                    if all(x is not None and x > 0 for x in xs):
                        out.append(max(xs) / min(xs))
                return out
            for label, key in (("tool calls", "n_calls"), ("agent time", "duration_s"),
                               ("log-est. input", "est_in_tok"), ("provider input", "in_tok")):
                sp = spread(key, True)
                mark = flag if key == "in_tok" else ""
                rows[f"{label}: same-outcome cells at 2x+"][h] = frac(
                    sum(x >= 2 for x in sp), len(sp)) + mark
                rows[f"  median max/min, {label}"][h] = (
                    f"{statistics.median(sp):.2f}x" if sp else "-") + mark

            # what one pass costs: tokens spent on every trial (passed or not) / passes
            tk = [t for t in ts if t.get("in_tok")]
            passes = len(tk) - failed(tk)
            rows["provider counts: prompt never shrinks"][h] = (
                f"{100 * share:.0f}% " + ("usable" if not flag else "NOT usable")
                if share is not None else "no per-step counts !")
            cal = [t["in_tok"] / t["est_in_tok"] for t in lg
                   if t.get("in_tok") and t.get("est_in_tok")]
            rows["  provider / log-estimated input, median"][h] = (
                f"{statistics.median(cal):.2f}" if cal else "-") + flag
            le = [t for t in lg if t.get("est_in_tok")]
            lpass = len(le) - failed(le)
            rows["log-est. input spent per pass (M tok)"][h] = (
                f"{sum(t['est_in_tok'] for t in le) / lpass / 1e6:.2f}" if lpass else "-")
            rows["trials with tokens recorded"][h] = frac(len(tk), len(ts))
            rows["provider input spent per pass (M)"][h] = (
                f"{sum(t['in_tok'] for t in tk) / passes / 1e6:.2f}" if passes else "-") + flag
            rows["output tokens spent per pass (k)"][h] = (
                f"{sum(t.get('out_tok') or 0 for t in tk) / passes / 1e3:.0f}" if passes else "-")
            ck = [t["cache_tok"] / t["in_tok"] for t in tk if t.get("cache_tok") is not None]
            rows["  cached share of input, median"][h] = (
                f"{100 * statistics.median(ck):.0f}% (n={len(ck)})" if ck else "not recorded")
            dc = [t for t in ts if t.get("cost_usd") is not None]
            dpass = len(dc) - failed(dc)
            rows["trials with dollar cost recorded"][h] = frac(len(dc), len(ts))
            rows["  recorded dollars spent per pass"][h] = (
                f"{sum(float(t['cost_usd']) for t in dc) / dpass:.2f}" if dpass else "-")
        return rows

    def print_rows(rows):
        say(f"{'measure':<40} " + " ".join(f"{h[:15]:>15}" for h in labels))
        for name, vals in rows.items():
            say(f"{name:<40} " + " ".join(f"{vals.get(h, '-'):>15}" for h in labels))

    scopes = [("all tasks", lambda t: True)]
    if revised:
        scopes.append(("tasks Terminal-Bench 2.1 did not revise", lambda t: not t["revised"]))

    section("7. Candidate measures per arm (count/of and share), all tasks")
    say(f"cells = (task, arm) with >= {MIN_LOGGED} logged runs. 'stopped' = no exception recorded.")
    say("per pass = tokens spent on every trial, passed or not, divided by the passes.")
    say(f"! = provider input counts fail the rule (prompt does not shrink in >= {TOKEN_RULE:.0%} "
        "of step pairs) or cannot be checked (no per-step counts); do not use.")
    say("log-est. input = characters in the log before each model call / 4, summed (estimate).")
    print_rows(measure_rows(scopes[0][1]))
    if revised:
        n_un = len({t["task"] for t in trials_rows if not t["revised"]})
        section(f"7b. Same measures on the {n_un} tasks Terminal-Bench 2.1 did not revise")
        print_rows(measure_rows(scopes[1][1]))

    ref = next((h for h in labels if h in REFERENCE_ARMS), labels[0])
    if len(labels) > 1:
        section(f"8. Paired by task: {ref} against each other arm (all trials)")
        say("a task is 'every' if the arm passed all its trials, 'never' if none, else 'some'")
        say("ratios: tasks both arms pass every time; per task, other arm's median / "
            f"{ref}'s median; then the median over tasks")
        grades = ("every", "some", "never")

        def task_ratio(xs, ys, key):
            a, b = med([t.get(key) for t in xs]), med([t.get(key) for t in ys])
            return b / a if a and b else None

        for scope, keep in scopes:
            per = {h: defaultdict(list) for h in labels}
            for t in trials_rows:
                if keep(t):
                    per[t["arm"]][t["task"]].append(t)
            for h in labels:
                if h == ref:
                    continue
                tasks = sorted(set(per[ref]) & set(per[h]))
                g = {k: (grade(per[ref][k]), grade(per[h][k])) for k in tasks}
                tab = Counter(g.values())
                say(f"-- {scope}: {ref} (rows) against {h} (columns), {len(tasks)} tasks")
                say(f"   {'':<8} " + " ".join(f"{c:>6}" for c in grades))
                for r in grades:
                    say(f"   {r:<8} " + " ".join(f"{tab[(r, c)]:>6}" for c in grades))
                n_ref = sum(tab[("every", c)] for c in grades)
                say(f"   of the {n_ref} tasks {ref} passes every time, {h} passes every time on "
                    f"{frac(tab[('every', 'every')], n_ref)}")
                n_oth = sum(tab[(r, "every")] for r in grades)
                say(f"   of the {n_oth} tasks {h} passes every time, {ref} passes every time on "
                    f"{frac(tab[('every', 'every')], n_oth)}")
                both = [k for k in tasks if g[k] == ("every", "every")]
                parts = []
                for label, key in (("log-est. input", "est_in_tok"),
                                   ("provider input (see ! in 7)", "in_tok"),
                                   ("output tokens", "out_tok"),
                                   ("agent time", "duration_s"), ("tool calls", "n_calls")):
                    rs = [x for x in (task_ratio(per[ref][k], per[h][k], key) for k in both)
                          if x is not None]
                    parts.append(f"{label} {statistics.median(rs):.2f}x (n={len(rs)})"
                                 if rs else f"{label} -")
                say(f"   both pass every time ({len(both)} tasks), {h} / {ref}: " + ", ".join(parts))
                lost = [k for k in tasks if g[k] == ("every", "never")]
                gained = [k for k in tasks if g[k] == ("never", "every")]
                say(f"   {ref} every, {h} never ({len(lost)}): " + (", ".join(lost[:15]) or "none"))
                say(f"   {ref} never, {h} every ({len(gained)}): " + (", ".join(gained[:15]) or "none"))

    # ── 9. pre-registered hypotheses, scored mechanically ────────────────────
    TH = THRESHOLDS
    section("9. Pre-registered hypotheses, scored mechanically")
    say("thresholds: " + ", ".join(f"{k}={v}" for k, v in TH.items()))

    def need(n, share):
        return max(1, math.ceil(share * n - 1e-9))

    def rate(x):
        return x[0] / x[1] if x[1] else None

    def show(x):
        return f"{x[0]}/{x[1]} {pct(*x)}" if x[1] else "-"

    def verdict(name, holds, k, extra=""):
        n = sum(holds.values())
        say(f"   {name}: {'SUPPORTED' if n >= k else 'NOT SUPPORTED'}, holds for {n} of "
            f"{len(holds)} (needs {k}){extra}")

    def arm_stats(h, keep):
        ts = [t for t in by_h[h] if keep(t)]
        lg = [t for t in ts if t["has_log"]]
        by_task, cells = defaultdict(list), defaultdict(list)
        for t in ts:
            by_task[t["task"]].append(t)
        for t in lg:
            cells[t["task"]].append(t)
        cells = {k: v for k, v in cells.items() if len(v) >= MIN_LOGGED}
        stopped = [t for t in ts if t["exception_type"] is None]
        le = [t for t in lg if t.get("est_in_tok")]
        lpass = len(le) - failed(le)
        same = [v for v in cells.values() if len({t["verdict"] for t in v}) == 1
                and all((t.get("est_in_tok") or 0) > 0 for t in v)]
        wide = sum(max(t["est_in_tok"] for t in v) / min(t["est_in_tok"] for t in v)
                   >= TH["M5_spread"] for v in same)
        return {"grade": {k: grade(v) for k, v in by_task.items()}, "cells": cells, "trials": ts,
                "flip": (sum(len({t["verdict"] for t in v}) == 2 for v in cells.values()),
                         len(cells)),
                "stopfail": (failed(stopped), len(stopped)),
                "per_pass": sum(t["est_in_tok"] for t in le) / lpass if lpass else None,
                "spread": (wide, len(same))}

    primary = scopes[-1][0]                  # the unrevised tasks when the list is on disk
    for scope, keep in scopes:
        say(f"-- scope: {scope}" + ("   [PRIMARY]" if scope == primary else "   [alongside]"))
        st = {h: arm_stats(h, keep) for h in labels}
        if args.axis == "model":
            others = [h for h in labels if h != ref]
            k = need(len(others), TH["model_arms_share"])
            holds = {m: {} for m in ("M1", "M2", "M3", "M4")}
            for h in others:
                tasks = set(st[ref]["grade"]) & set(st[h]["grade"])
                ref_every = [t for t in tasks if st[ref]["grade"][t] == "every"]
                held = [t for t in ref_every if st[h]["grade"][t] == "every"]
                not_held = [t for t in ref_every if st[h]["grade"][t] != "every"]
                some = [t for t in not_held if st[h]["grade"][t] == "some"]
                m1 = (len(held), len(ref_every))
                m2 = (len(some), len(not_held))
                r0, r1 = rate(st[ref]["stopfail"]), rate(st[h]["stopfail"])
                p0, p1 = st[ref]["per_pass"], st[h]["per_pass"]
                m4 = p1 / p0 if p0 and p1 else None
                holds["M1"][h] = rate(m1) is not None and rate(m1) < TH["M1_max_hold"]
                holds["M2"][h] = rate(m2) is not None and rate(m2) >= TH["M2_min_some"]
                holds["M3"][h] = (r0 is not None and r1 is not None
                                  and r1 >= TH["M3_ratio"] * r0 and r1 - r0 >= TH["M3_points"])
                holds["M4"][h] = m4 is not None and m4 >= TH["M4_ratio"]
                say(f"   {h} against {ref}: M1 held {show(m1)}; M2 'some' among not held "
                    f"{show(m2)}; M3 stopped-but-failed {show(st[h]['stopfail'])} against "
                    f"{show(st[ref]['stopfail'])}; M4 log-est. input per pass "
                    f"{(f'{m4:.2f}x' if m4 else '-')}"
                    f" ({(p1 or 0) / 1e6:.2f}M against {(p0 or 0) / 1e6:.2f}M)")
            for m in ("M1", "M2", "M3", "M4"):
                extra = ""
                if "GLM-5" in holds[m]:
                    rest = {h: v for h, v in holds[m].items() if h != "GLM-5"}
                    extra = f"; without GLM-5: {sum(rest.values())} of {len(rest)}"
                verdict(m, holds[m], k, extra)
            m5 = {h: (rate(st[h]["spread"]) is not None
                      and rate(st[h]["spread"]) >= TH["M5_min_share"]) for h in labels}
            say("   M5 same-outcome tasks at 2x+ log-est. input: " + ", ".join(
                f"{h} {show(st[h]['spread'])}" for h in labels))
            verdict("M5", m5, len(labels))
        else:
            k = need(len(labels), TH["harness_arms_share"])
            h1 = {h: rate(st[h]["flip"]) is not None and rate(st[h]["flip"]) >= TH["H1_min_flip"]
                  for h in labels}
            h2 = {h: (rate(st[h]["stopfail"]) is not None
                      and rate(st[h]["stopfail"]) >= TH["H2_min_stopfail"]) for h in labels}
            say("   H1 tasks that flip: " + ", ".join(f"{h} {show(st[h]['flip'])}" for h in labels))
            verdict("H1", h1, k)
            say("   H2 stopped by itself, but failed: " + ", ".join(
                f"{h} {show(st[h]['stopfail'])}" for h in labels))
            verdict("H2", h2, k)
            common = set.intersection(*(set(st[h]["cells"]) for h in labels)) if labels else set()
            flips, stops = {}, {}
            for h in labels:
                flips[h] = (sum(len({t["verdict"] for t in st[h]["cells"][c]}) == 2
                                for c in common), len(common))
                sp = [t for t in st[h]["trials"]
                      if t["task"] in common and t["exception_type"] is None]
                stops[h] = (failed(sp), len(sp))
            say(f"   H3 on the {len(common)} tasks with a cell in every harness:")
            ok = {}
            for name, d in (("flip rate", flips), ("stopped-but-failed rate", stops)):
                rs = {h: rate(d[h]) for h in labels if rate(d[h]) is not None}
                if len(rs) < 2:
                    ok[name] = False
                    say(f"      {name}: not enough harnesses")
                    continue
                lo, hi = min(rs.values()), max(rs.values())
                ok[name] = (hi / lo >= TH["H3_ratio"]) if lo > 0 else hi >= TH["H3_zero_floor"]
                say(f"      {name}: " + ", ".join(f"{h} {show(d[h])}" for h in labels)
                    + f"; highest / lowest {(f'{hi / lo:.2f}x' if lo > 0 else 'lowest is zero')}"
                    + f" -> {'holds' if ok[name] else 'does not hold'}")
            say(f"   H3: {'SUPPORTED' if all(ok.values()) else 'NOT SUPPORTED'} (needs both)")

    # ── DuckDB index (CSV if duckdb is missing) ──────────────────────────────
    import pandas as pd
    drop = {"submission"}
    tdf = pd.DataFrame([{k: v for k, v in t.items() if k not in drop} for t in trials_rows])
    cdf = pd.DataFrame(calls_rows)
    out_dir.mkdir(exist_ok=True)
    db = out_dir / "harbor.duckdb"
    try:
        import duckdb
        con = duckdb.connect(str(db))
        con.register("tdf", tdf)
        con.execute(f"CREATE OR REPLACE TABLE trials_{tag} AS SELECT * FROM tdf")
        if len(cdf):
            con.register("cdf", cdf)
            con.execute(f"CREATE OR REPLACE TABLE calls_{tag} AS SELECT * FROM cdf")
        con.close()
        where = f"{rel(db)} (trials_{tag}, calls_{tag})"
    except Exception as e:
        tdf.to_csv(out_dir / f"harbor_trials_{tag}.csv", index=False)
        cdf.to_csv(out_dir / f"harbor_calls_{tag}.csv", index=False)
        where = f"CSV files in {rel(out_dir)} (duckdb not used: {type(e).__name__}: {e})"

    say()
    say(f"runs written: {sum(t['has_log'] for t in trials_rows):,} under {rel(runs_dir)}")
    say(f"index: {where}")
    report = out_dir / f"harbor_import_{tag}.txt"
    report.write_text("\n".join(lines_out) + "\n")
    say(f"report: {rel(report)}")


if __name__ == "__main__":
    main()