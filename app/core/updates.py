"""Finding out a new version exists, and installing it.

Copied from File Manager's `app/core/updates.py` with the repository, the
AppId and the folder names changed, so the two can be diffed. The comments
below are File Manager's and hold here word for word: one GET of a small JSON
manifest, at most once a launch, nothing downloaded that was not agreed to,
the download checked against the manifest's size and SHA-256, and no update at
all for a copy that was not installed.

One thing is different here. PLC Monitor goes onto plant PCs that often have
no route to the internet, so a check that fails says nothing unless somebody
asked for it -- which is also how File Manager behaves, and is the point.

The only other call to the internet is the plctag.dll download, which happens
when somebody presses its button. Everything here is deliberately narrow: one GET of one small JSON file, at most once a launch
unless the user asks, and no download that was not agreed to.

**None of it runs on the UI thread.** The rule the rest of the application
follows for the filesystem holds twice over for a socket: a GET against an
unreachable host blocks for as long as the connect timeout, and a window that
stops painting because GitHub is slow is the same defect this application was
written to escape, arriving by a different route. The two workers below are
threads rather than processes because unlike a listing there is nothing here
that a hung SMB share can wedge -- the timeout is ours to set, the socket is
ours to close, and the only file touched is one we created in `%LOCALAPPDATA%`.

What is downloaded is checked against the size and the SHA-256 in the manifest,
and written to a `.part` file that is renamed onto the real name only once both
match -- the same discipline the transfer engine uses, for the same reason.
That proves the download arrived intact. It does not prove who built it: the
installer is unsigned, so the trust boundary is HTTPS to github.com and nothing
more. That is a choice rather than an oversight, written down here so it does
not quietly become one. Signing the installer is the upgrade path if this ever
runs on a machine that is not the author's.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, QTimer, Signal

REPO = "Robbuie/plc-monitor"

#: The manifest. `releases/latest/download/` always resolves to the newest
#: release, so this URL never changes and no release-listing call is needed.
FEED = f"https://github.com/{REPO}/releases/latest/download/latest.json"

#: Only a URL under this prefix is ever fetched. The manifest is data from the
#: network, and data from the network does not get to say what this application
#: downloads and runs.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"

USER_AGENT = "PLCMonitor-Updater"

#: Seconds. Short: a check that has not answered by now can wait for the next
#: launch, and nothing is waiting on the answer.
FEED_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 60.0

#: Ceilings, so a wrong or hostile URL cannot fill the disk. The manifest is a
#: few hundred bytes and the installer is tens of megabytes.
MAX_FEED_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 500 * 1024 * 1024

#: Long enough that the check never competes with the first listing, short
#: enough that a session started in the morning still sees it.
LAUNCH_DELAY_MS = 8000

CHUNK = 1 << 18


class UpdateError(Exception):
    """Anything that stopped a check or a download, in words worth showing."""


@dataclass(frozen=True)
class Release:
    """One entry from the manifest, once it has been found to make sense."""

    version: str
    name: str
    url: str
    size: int
    sha256: str
    notes: str = ""
    page: str = ""


# --------------------------------------------------------------------------
# The parts with no Qt and no network in them, which is where the decisions
# live and therefore what the tests are about.
# --------------------------------------------------------------------------


def is_newer(candidate: str, current: str) -> bool:
    """Dotted-numeric comparison, which is all a release tag of ours ever is.

    Anything unparseable sorts as "not newer". A malformed feed should be inert
    rather than a prompt: the failure mode of guessing wrong here is telling
    somebody to install a version that does not exist.
    """
    def parts(value: str) -> list[int] | None:
        head = str(value or "").strip().lstrip("v").split("-")[0]
        pieces = head.split(".")
        if not pieces or not all(piece.isdigit() for piece in pieces):
            return None
        return [int(piece) for piece in pieces]

    left, right = parts(candidate), parts(current)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    left += [0] * (width - len(left))
    right += [0] * (width - len(right))
    return left > right


def parse(payload: object) -> Release:
    """Turn the manifest into a `Release`, or refuse it.

    Every field is checked, including the ones that "obviously" hold: this is
    the one piece of input the application takes from somewhere other than the
    user, and the thing it eventually does with it is run an executable.
    """
    if not isinstance(payload, dict):
        raise UpdateError("the update manifest is not an object")
    installer = payload.get("installer")
    if not isinstance(installer, dict):
        raise UpdateError("the update manifest names no installer")

    version = str(payload.get("version", "")).strip()
    name = str(installer.get("name", "")).strip()
    url = str(installer.get("url", "")).strip()
    sha256 = str(installer.get("sha256", "")).strip().lower()
    size = installer.get("size")

    if not version:
        raise UpdateError("the update manifest has no version")
    if not name or name != os.path.basename(name) or not name.lower().endswith(".exe"):
        raise UpdateError(f"the update manifest names a bad installer: {name!r}")
    if not url.startswith(DOWNLOAD_PREFIX) or not url.endswith("/" + name):
        raise UpdateError("the update manifest points somewhere other than this repository's releases")
    # A prefix test alone is not the guarantee it reads as. `urllib` sends a
    # path as it is given, dot segments and all, and the server is what
    # resolves them -- so `.../releases/download/v1/../../../elsewhere.exe`
    # starts with the prefix, ends with the name, and asks GitHub for a file
    # in another repository. The download is checked against the manifest's
    # own hash, which proves nothing when the manifest is what is wrong, so
    # this check is the one that has to hold.
    if _traverses(url):
        raise UpdateError("the update manifest points outside this repository's releases")
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
        raise UpdateError("the update manifest has no usable checksum")
    if not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_BYTES:
        raise UpdateError("the update manifest has no usable size")

    return Release(
        version=version,
        name=name,
        url=url,
        size=size,
        sha256=sha256,
        notes=str(payload.get("notes", "")),
        page=str(payload.get("release", "")),
    )


def _traverses(url: str) -> bool:
    """Whether a URL's path walks upwards, in any of the spellings it can use.

    Percent-encoded as well as plain: the server decodes before it resolves, so
    a check that reads only the literal dots is reading the wrong string. Any
    `%2e` at all is refused rather than decoded and re-examined, because a
    release asset of ours has no business carrying one and "refuse what is not
    understood" is the cheaper half of that trade.
    """
    path = url.split("#", 1)[0].split("?", 1)[0].lower()
    if "%2e" in path:
        return True
    return any(segment in ("..", ".") for segment in path.split("/"))


#: The AppId in `packaging/installer.iss`, which is what Inno Setup names its
#: uninstall key after. If one of them changes the other has to, and an install
#: that no longer recognises itself stops updating rather than misbehaving.
APP_ID = "{3D752E85-572B-48DF-906D-89C68C5F385B}"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def windows() -> bool:
    return os.name == "nt"


def app_folder() -> str | None:
    """The folder this executable is actually running from."""
    if not frozen():
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


def install_location() -> str | None:
    """Where the installer put this application, according to Windows.

    Inno Setup writes `InstallLocation` under its own uninstall key as part of
    installing, so this is the installer's answer rather than a guess. Per-user
    installs land in HKCU and the all-users option in HKLM; both are checked
    because the setup allows either.
    """
    if not windows():
        return None
    import winreg

    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, UNINSTALL_KEY) as key:
                value, _ = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if value:
            return os.path.normpath(str(value))
    return None


def same_folder(left: str | None, right: str | None) -> bool:
    """Whether two paths name the same folder, short names and links included."""
    if not left or not right:
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.normpath(left)) == \
               os.path.normcase(os.path.normpath(right))


def unavailable() -> str | None:
    r"""Non-None when updating cannot work at all, with the reason to show.

    The interesting case is the third one. An update installs into the folder
    the installer owns, so a copy running from anywhere else -- the unpacked
    `dist\PLCMonitor\` folder, or a copy someone moved onto a stick -- would
    download an update, install it somewhere it is not, and go on running the
    old version while offering the same update on every launch. Checking the
    running folder against what the installer recorded is what stops that, and
    it has to be a check rather than an assumption because being frozen and
    being installed are not the same thing.
    """
    if not frozen():
        return "Updates only run from an installed build."
    if not windows():
        return "Updates only run on Windows."
    installed = install_location()
    if installed is None:
        return "This copy was not installed, so there is nothing for an update to replace."
    if not same_folder(app_folder(), installed):
        return (f"This copy runs from {app_folder()}, but the installed one is in "
                f"{installed}. An update would go there rather than here.")
    return None


def staging_folder() -> str:
    """Where a downloaded installer waits. Local, per user, and disposable.

    Not the temp folder: an installer that has been downloaded but not yet run
    should survive a cleanup, and the uninstaller removes this folder by name.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "PLC Monitor", "updates")


# --------------------------------------------------------------------------
# The parts that do touch the network and the disk. Called from a thread,
# never from the UI.
# --------------------------------------------------------------------------


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - prefix checked


def fetch(url: str = FEED, *, timeout: float = FEED_TIMEOUT) -> Release:
    """GET the manifest and make sense of it, or raise `UpdateError`."""
    try:
        with _open(url, timeout) as response:
            raw = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("No releases have been published yet.") from error
        raise UpdateError(f"GitHub answered {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateError("Could not reach GitHub.") from error

    if len(raw) > MAX_FEED_BYTES:
        raise UpdateError("The update manifest is implausibly large.")
    try:
        return parse(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("The update manifest could not be read.") from error


def download(release: Release, *, folder: str | None = None,
             progress=None, cancel: threading.Event | None = None) -> str:
    """Fetch the installer, verify it, and return where it landed.

    Written to `<name>.part` and renamed onto `<name>` only after the size and
    the hash both match, so a cancelled or interrupted download leaves nothing
    that looks finished.
    """
    if not release.url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("refusing to download from outside the releases page")

    target_folder = folder or staging_folder()
    try:
        os.makedirs(target_folder, exist_ok=True)
    except OSError as error:
        raise UpdateError("Could not create the download folder.") from error

    # Whatever is in there is from a release that is no longer the one being
    # installed, and this is the moment it is certainly not in use: the new
    # download has not started and the old installer was never run by this
    # session. On the download thread, like everything else below this line.
    sweep(target_folder)

    final = os.path.join(target_folder, release.name)
    partial = final + ".part"

    hasher = hashlib.sha256()
    written = 0
    try:
        with _open(release.url, DOWNLOAD_TIMEOUT) as response, open(partial, "wb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("cancelled")
                block = response.read(CHUNK)
                if not block:
                    break
                written += len(block)
                if written > release.size:
                    raise UpdateError("The download is larger than the manifest said.")
                hasher.update(block)
                handle.write(block)
                if progress is not None:
                    progress(written, release.size)
    except UpdateError:
        _discard(partial)
        raise
    except (urllib.error.URLError, OSError, ValueError) as error:
        _discard(partial)
        raise UpdateError("The download did not finish.") from error

    if written != release.size or hasher.hexdigest() != release.sha256:
        _discard(partial)
        raise UpdateError("The download did not match its checksum and was discarded.")

    try:
        os.replace(partial, final)
    except OSError as error:
        _discard(partial)
        raise UpdateError("The download could not be saved.") from error
    return final


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def sweep(folder: str | None = None, *, keep: str = "") -> int:
    """Delete installers left in the staging folder. Returns how many went.

    Nothing did this until 0.29.12 and the arithmetic is why it is worth a
    function: the installer is tens of megabytes, every accepted update leaves
    one behind, and the folder is never looked at again by anybody -- so the
    cost of updating was a permanent thirty-odd megabytes of `%LOCALAPPDATA%`
    per release, growing for as long as the application kept working properly.

    `keep` is the one staged this session, which is waiting to be run on the
    way out and must survive a sweep that happens to run after it landed.
    `.part` files go too: one of those is a download that did not finish, and
    an unfinished download is never resumed here -- it is started again.
    """
    target = folder or staging_folder()
    keeping = os.path.normcase(os.path.abspath(keep)) if keep else ""
    removed = 0
    try:
        entries = list(os.scandir(target))
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file():
            continue
        lowered = entry.name.lower()
        if not (lowered.endswith(".exe") or lowered.endswith(".part")):
            continue
        if keeping and os.path.normcase(os.path.abspath(entry.path)) == keeping:
            continue
        try:
            os.remove(entry.path)
        except OSError:
            continue
        removed += 1
    return removed


def launch(installer: str) -> bool:
    """Run the staged installer and return whether it started.

    Silent, because the user already agreed to this, and detached, because this
    process is about to exit and must not be the installer's parent. Inno Setup
    waits for the old executable to let go of its files rather than failing on
    them, and does not relaunch anything: coming back up is the user's move.
    """
    if not os.path.isfile(installer):
        return False
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        # The log beside the installer: if an update ever fails, it says why.
        log = os.path.splitext(installer)[0] + ".log"
        subprocess.Popen(  # noqa: S603 - our own file, in our own folder
            [installer, "/SILENT", "/NORESTART", "/SUPPRESSMSGBOXES", f"/LOG={log}"],
            creationflags=flags, close_fds=True,
        )
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# The threads, and the object the window talks to.
# --------------------------------------------------------------------------


class _Check(QThread):
    """One GET of the manifest, and the tidying that goes with it.

    The sweep is here rather than anywhere tidier because this is the only
    thing that runs off the UI thread early in a session. An installer left in
    the staging folder is one a previous session downloaded: this session will
    not run it -- what is staged is only ever what was fetched since the window
    opened -- so by the time a check is being made it is thirty megabytes of
    nothing. Failing to remove it is not worth reporting.
    """

    found = Signal(object)      # Release
    failed = Signal(str)

    def run(self) -> None:      # noqa: D102 - QThread
        try:
            sweep()
        except OSError:
            pass
        try:
            release = fetch()
        except UpdateError as error:
            self.failed.emit(str(error))
            return
        self.found.emit(release)


class _Download(QThread):
    """One installer, verified."""

    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, release: Release, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._release = release
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:      # noqa: D102 - QThread
        try:
            path = download(self._release,
                            progress=lambda done, total: self.progress.emit(done, total),
                            cancel=self._cancel)
        except UpdateError as error:
            if not self._cancel.is_set():
                self.failed.emit(str(error))
            return
        self.done.emit(path)


class Updates(QObject):
    """The state around the two threads: what was found, what is staged.

    The window connects to the signals and owns every dialog. Nothing in here
    shows anything -- an update check that decides on its own to interrupt
    somebody mid-transfer is a check that gets turned off.
    """

    available = Signal(object)      # Release, newer than this build
    uptodate = Signal(str)          # the current version
    problem = Signal(str)
    progress = Signal(int, int)
    ready = Signal(object)          # Release, downloaded and verified
    busy_changed = Signal(bool)

    def __init__(self, config, version: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._version = version
        self._check: _Check | None = None
        self._download: _Download | None = None
        self._manual = False
        self._staged: str | None = None

    # ------------------------------------------------------------- checking

    @property
    def busy(self) -> bool:
        return bool((self._check and self._check.isRunning())
                    or (self._download and self._download.isRunning()))

    @property
    def staged(self) -> str | None:
        """The installer waiting for the application to quit, if there is one."""
        return self._staged

    def start_if_wanted(self) -> None:
        """The one automatic check, some seconds after the window appears."""
        if not self._config.get("updates.check"):
            return
        if unavailable():
            return
        QTimer.singleShot(LAUNCH_DELAY_MS, lambda: self.check(manual=False))

    def check(self, *, manual: bool) -> None:
        reason = unavailable()
        if reason:
            if manual:
                self.problem.emit(reason)
            return
        if self.busy:
            return
        self._manual = manual
        self._check = _Check(self)
        self._check.found.connect(self._on_found)
        self._check.failed.connect(self._on_failed)
        self._check.finished.connect(lambda: self.busy_changed.emit(self.busy))
        self._check.start()
        self.busy_changed.emit(True)

    def _on_found(self, release: Release) -> None:
        if not is_newer(release.version, self._version):
            if self._manual:
                self.uptodate.emit(self._version)
            return
        # A skipped version stays skipped until a newer one appears or the user
        # asks. Asking again about the release somebody has already declined is
        # how a prompt becomes noise.
        if not self._manual and release.version == self._config.get("updates.skip_version"):
            return
        self.available.emit(release)

    def _on_failed(self, message: str) -> None:
        # A failed check on launch says nothing. There is no version of "could
        # not reach GitHub" that is worth interrupting somebody for; a check
        # they asked for is different.
        if self._manual:
            self.problem.emit(message)

    # ------------------------------------------------------------ downloading

    def accept(self, release: Release) -> None:
        if self.busy:
            return
        self._download = _Download(release, self)
        self._download.progress.connect(self.progress)
        self._download.done.connect(lambda path, r=release: self._on_downloaded(path, r))
        self._download.failed.connect(self.problem)
        self._download.finished.connect(lambda: self.busy_changed.emit(self.busy))
        self._download.start()
        self.busy_changed.emit(True)

    def skip(self, release: Release) -> None:
        self._config.set("updates.skip_version", release.version)

    def cancel(self) -> None:
        if self._download is not None:
            self._download.cancel()

    def _on_downloaded(self, path: str, release: Release) -> None:
        self._staged = path
        self.ready.emit(release)

    # ---------------------------------------------------------------- closing

    def shutdown(self) -> None:
        """Stop whatever is in flight. Called before the window goes."""
        if self._download is not None:
            self._download.cancel()
        for thread in (self._check, self._download):
            if thread is not None and thread.isRunning():
                thread.wait(3000)

    def install_staged(self) -> bool:
        """Run the staged installer, if one is waiting. Called on the way out."""
        if not self._staged:
            return False
        return launch(self._staged)
