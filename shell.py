"""Guarded terminal access for Jarvis.

The user asked Jarvis to *use* the terminal. Speech is unreliable, so a command
passes through three layers before it runs:

1. **Refused outright** -- privilege escalation (``sudo``/``su``/``pkexec``) and
   catastrophic commands (``rm -rf``, ``mkfs``, ``dd ... of=/dev/...``, fork
   bombs, piping a download straight into a shell). Jarvis never becomes root.
2. **Confirmed first** -- anything that mutates state (``rm``, ``mv``, ``chmod``,
   ``apt``, ``systemctl``, writing with ``>``, ``git push``, ...). The user hears
   the exact command and must say "yes" (:mod:`confirm`).
3. **Runs** -- everything else (``ls``, ``cat``, ``grep``, ``df``, ``git status``,
   ...), with a timeout and a capped output size.

Nothing here is aware of the speaker: the guardrails are enforced in code.
"""

import os
import re
import subprocess

import confirm
from config import SHELL_MAX_OUTPUT, SHELL_TIMEOUT

# --------------------------------------------------------------------------- #
# layer 1: refused outright
# --------------------------------------------------------------------------- #

_DENY = [
    (re.compile(r"\b(sudo|doas|pkexec)\b", re.I),
     "I never run commands as root."),
    (re.compile(r"(^|[\s;&|])su(\s|$)", re.I),
     "I never switch to another user."),
    # --- power / device control: the terminal must never shut the device down ---
    # The "-"/"_"/" " separators are tolerated so "power-off", "power_off" and
    # "power off" are all caught, not just the joined spelling.
    (re.compile(r"\b(shut[\s_-]?down|power[\s_-]?off|reboot|halt|telinit)\b", re.I),
     "I never shut down or restart the device from the terminal."),
    (re.compile(r"\binit\s+[06]\b", re.I),
     "I never shut down or restart the device from the terminal."),
    (re.compile(r"\bsystemctl\s+(poweroff|reboot|halt|suspend|hibernate|"
                r"hybrid-sleep|kexec|isolate|rescue|emergency|default)\b", re.I),
     "I never shut down or restart the device from the terminal."),
    (re.compile(r"\bloginctl\s+(poweroff|reboot|suspend|hibernate|"
                r"terminate-session|terminate-user|terminate-seat)\b", re.I),
     "I never end your session from the terminal."),
    (re.compile(r"\b(pm-suspend|pm-hibernate|rtcwake|suspend|hibernate)\b", re.I),
     "I never suspend or hibernate the device from the terminal."),
    (re.compile(r"\b(log[\s_-]?out|sign[\s_-]?out)\b", re.I),
     "I never log you out from the terminal."),
    # Session managers that can end or power down the session by themselves.
    (re.compile(r"\b(gnome-session-quit|xfce4-session-logout|ksmserver|"
                r"cinnamon-session-quit|mate-session-save|lxqt-leave)\b", re.I),
     "I never end your session from the terminal."),
    (re.compile(r"\bkexec\b|\bsysrq\b|\bmagic-sysrq\b", re.I),
     "I won't touch the running kernel."),
    (re.compile(r">\s*/proc/sysrq-trigger", re.I),
     "I won't trigger a kernel magic sysrq."),
    (re.compile(r">\s*/sys/power/", re.I),
     "I won't touch the system power state."),
    # --- destructive filesystem / system commands ------------------------------ #
    (re.compile(r"\bno-preserve-root\b", re.I),
     "I won't touch the filesystem root."),
    (re.compile(r"\bmkfs(\.\w+)?\b|\bwipefs\b|\bparted\b|\bfdisk\b|\bsfdisk\b", re.I),
     "I won't repartition or format a disk."),
    (re.compile(r"\bdd\b[^|;&]*of=\s*/dev/", re.I),
     "I won't write directly to a device."),
    (re.compile(r">\s*/dev/(sd|nvme|hd|vd|mmcblk)", re.I),
     "I won't write directly to a device."),
    (re.compile(r":\s*\(\s*\)\s*\{.*\}\s*;?\s*:", re.S),
     "That looks like a fork bomb."),
    # Killing init/systemd (pid 1) takes the machine down with it.
    (re.compile(r"\b(kill|killall|pkill)\b[^|;&]*\b(init|systemd)\b", re.I),
     "I won't kill the init process."),
    (re.compile(r"\b(kill|killall|pkill)\b[^|;&]*\b1\b", re.I),
     "I won't kill the init process."),
    (re.compile(r"\b(passwd|chsh|chfn|visudo|chpasswd|usermod|useradd|userdel|"
                r"groupadd|groupdel|groupmod)\b", re.I),
     "I won't change accounts or credentials."),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|k|da)?sh\b", re.I),
     "I won't pipe a download straight into a shell."),
    # The same download handed to a shell with && or ; instead of a pipe.
    (re.compile(r"\b(curl|wget)\b[^|]*(?:&&|;)[^|]*\b(ba|z|k|da)?sh\b", re.I),
     "I won't run a freshly downloaded script."),
    (re.compile(r"\bchmod\s+-R\s+777\s+/(\s|$)", re.I),
     "That would open up the whole filesystem."),
]

# --------------------------------------------------------------------------- #
# layer 2: confirmed first
# --------------------------------------------------------------------------- #

_RISKY = [
    re.compile(r"\b(rm|rmdir|unlink)\b", re.I),
    re.compile(r"\b(mv|truncate|shred|dd|tee)\b", re.I),
    re.compile(r"\b(chmod|chown|chgrp|chattr|setfacl)\b", re.I),
    re.compile(r"\b(kill|killall|pkill)\b", re.I),
    re.compile(r"\b(apt|apt-get|dpkg|snap|flatpak|pacman|dnf|yum|brew)\b", re.I),
    re.compile(r"\bpip3?\s+(install|uninstall)\b|\bnpm\s+(install|uninstall|i)\b|"
               r"\byarn\b|\bcargo\s+install\b|\bgem\s+install\b", re.I),
    re.compile(r"\b(systemctl|service|crontab|at|mount|umount|swapoff|swapon)\b",
               re.I),
    re.compile(r"\b(ssh|scp|sftp|rsync|telnet|nc|ncat|socat)\b", re.I),
    re.compile(r"\b(iptables|nft|ufw|firewall-cmd)\b", re.I),
    # Windows destructive verbs: confirmed rather than run on a misheard word.
    re.compile(r"\b(del|erase|remove-item)\b", re.I),
    re.compile(r"\b(rd|rmdir)\s+/s\b", re.I),
    re.compile(r"\b(stop-computer|restart-computer|logoff)\b", re.I),
    re.compile(r"\b(diskpart|bcdedit|format|clear-disk|initialize-disk|"
               r"reg\s+delete)\b", re.I),
    re.compile(r"\b(docker|podman|virsh|systemd-nspawn|kubectl)\b", re.I),
    re.compile(r"\bfind\b[^|;&]*-delete\b", re.I),
    re.compile(r"\bgit\s+(reset\s+--hard|clean|push|checkout\s+--|branch\s+-D|"
               r"remote\s+(add|remove|set-url))\b", re.I),
    # Writing to a real file with > or >> (but not 2>&1, and not >/dev/null).
    re.compile(r">>?(?![&>])\s*(?!/(?:dev/)?null\b)\S"),
]

_MAX_LEN = 2000


def _is_risky(command):
    return any(pattern.search(command) for pattern in _RISKY)


def _variants(command):
    """The raw command plus a copy with quotes and backslashes removed.

    Stops trivial shell quoting from hiding a denied word: ``s\\hutdown`` and
    ``sh'utdown'`` both normalise back to ``shutdown``.
    """
    stripped = re.sub(r"[\"'\\]", "", command)
    return (command,) if stripped == command else (command, stripped)


def _recursive_force_rm(command):
    """True if any segment runs ``rm`` with both a recursive and a force flag.

    Written as a token walk rather than one regex so that ``-rf``, ``-r -f``,
    ``-fr`` and ``--recursive --force`` are all recognised, while a lone ``-f``
    (which still asks for confirmation) is not refused outright.
    """
    for segment in re.split(r"[|;&]+", command):
        tokens = segment.split()
        for index, token in enumerate(tokens):
            if token.rsplit("/", 1)[-1] != "rm":
                continue
            recursive = force = False
            for flag in tokens[index + 1:]:
                if not flag.startswith("-") or flag == "-":
                    continue                      # operand, not a flag
                if flag == "--":
                    continue
                if flag.startswith("--"):
                    recursive |= flag[2:] == "recursive"
                    force |= flag[2:] == "force"
                else:
                    recursive |= "r" in flag[1:] or "R" in flag[1:]
                    force |= "f" in flag[1:]
            if recursive and force:
                return True
    return False


def explain_refusal(command):
    """Return the refusal reason for ``command``, or None if it is allowed."""
    for candidate in _variants(command):
        for pattern, reason in _DENY:
            if pattern.search(candidate):
                return reason
    if _recursive_force_rm(command):
        return "A recursive force-delete is too dangerous."
    return None


def _shell_argv(command):
    """The native shell for this OS, as ``argv``.

    Windows has no bash (the ``bash`` on PATH is usually WSL, which may not have
    a distribution installed at all), so use the command processor that ships
    with Windows. Every other platform keeps the documented bash behaviour.
    """
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    return ["bash", "-c", command]


def _execute(command, cwd=None):
    """Run ``command`` in the platform shell, bounded by timeout and output cap."""
    workdir = os.path.expanduser(cwd) if cwd else None
    if workdir and not os.path.isdir(workdir):
        return f"There is no directory called {cwd}."
    try:
        proc = subprocess.run(
            _shell_argv(command),
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=SHELL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"The command ran longer than {SHELL_TIMEOUT}s and was stopped."
    except OSError as exc:
        return f"Could not run the command: {exc}"

    out = (proc.stdout or "").strip()
    if len(out) > SHELL_MAX_OUTPUT:
        out = out[:SHELL_MAX_OUTPUT] + "\n...[truncated]"
    if proc.returncode != 0:
        out = (out + f"\n(exit code {proc.returncode})").strip()
    return out or "(no output)"


def run(command, cwd=None):
    """Run a terminal command with guardrails. Returns output or a question."""
    command = (command or "").strip()
    if not command:
        return "Give me a command to run."
    if len(command) > _MAX_LEN:
        return "That command is too long."
    refusal = explain_refusal(command)
    if refusal:
        return f"I won't run that: {refusal}"
    if _is_risky(command):
        return confirm.request(
            lambda c=command, d=cwd: _execute(c, d),
            f"Run this command? {command}",
        )
    return _execute(command, cwd)
