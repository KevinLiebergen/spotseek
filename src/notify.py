"""Lets you know how a run went without opening the log: a Windows
notification when a run starts (if there's work) and when it ends, and a
summary of the last run in data/ultimo-resumen.txt.

The texts are in Spanish, as they're for you rather than for the log.
"""
import logging
import os
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger("spotseek")

SUMMARY_PATH = Path(config.STATE_DB_PATH).parent / "ultimo-resumen.txt"

# Uses PowerShell's own registered app id, so no app has to be registered
# for the notification to show. Title and text come in through environment
# variables to avoid any quoting trouble.
_TOAST_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$title = [Security.SecurityElement]::Escape($env:SPOTSEEK_TOAST_TITLE)
$text = [Security.SecurityElement]::Escape($env:SPOTSEEK_TOAST_TEXT)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$text</text></binding></visual></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def toast(title: str, text: str) -> None:
    """Shows a Windows notification. Never fails the run."""
    if not config.NOTIFY or os.name != "nt":
        return
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT],
            env={**os.environ, "SPOTSEEK_TOAST_TITLE": title, "SPOTSEEK_TOAST_TEXT": text},
            capture_output=True,
            timeout=30,
        )
    except Exception:
        log.exception("Couldn't show a notification")


_OUTCOME_LABELS = {
    "duplicate": "ya las tenías",
    "not_found": "no encontradas",
    "download_failed": "descarga fallida",
    "file_not_found": "archivo no encontrado",
    "skipped_long": "sesiones saltadas",
    "error": "con error",
}


@dataclass
class RunSummary:
    started: float = field(default_factory=time.time)
    new: int = 0
    retries: int = 0
    tracks: list[tuple[str, str]] = field(default_factory=list)  # (artist - title, outcome)
    problem: str = ""  # why the run stopped early, if it did
    rekordbox: str = ""

    def add(self, name: str, outcome: str) -> None:
        self.tracks.append((name, outcome))

    def counts(self) -> Counter:
        return Counter("ok" if o.startswith("ok") else o for _, o in self.tracks)

    def headline(self) -> str:
        """One line for the notification."""
        if not self.tracks and not self.problem:
            return "Sin likes nuevos." + (f" {self.rekordbox}" if self.rekordbox else "")
        counts = self.counts()
        filed = sum(1 for _, o in self.tracks if o.startswith("ok:"))
        parts = []
        if counts["ok"]:
            detail = f" ({filed} en su carpeta, {counts['ok'] - filed} sin clasificar)" if counts["ok"] != filed else ""
            parts.append(f"{counts['ok']} descargadas{detail}")
        parts += [f"{counts[k]} {label}" for k, label in _OUTCOME_LABELS.items() if counts[k]]
        text = ", ".join(parts) or "Nada procesado"
        if self.problem:
            text += f". Parado: {self.problem}"
        if self.rekordbox:
            text += f". {self.rekordbox}"
        return text

    def write(self) -> None:
        minutes = (time.time() - self.started) / 60
        lines = [
            f"Ejecución del {datetime.fromtimestamp(self.started):%d/%m/%Y %H:%M} ({minutes:.0f} min)",
            f"Likes nuevos: {self.new} | reintentos: {self.retries}",
            "",
            self.headline(),
            "",
        ]
        groups = [
            ("Descargadas", lambda o: o.startswith("ok"), lambda o: o[3:] if o.startswith("ok:") else "sin clasificar, en la raíz"),
        ] + [(label.capitalize(), lambda o, k=k: o == k, None) for k, label in _OUTCOME_LABELS.items()]
        for label, matches, where in groups:
            names = [(n, o) for n, o in self.tracks if matches(o)]
            if names:
                lines.append(f"{label} ({len(names)}):")
                lines += [f"  - {n}" + (f"  ->  {where(o)}" if where else "") for n, o in names]
                lines.append("")
        lines.append(f"Detalles en {Path(config.STATE_DB_PATH).parent / 'spotseek.log'}")
        try:
            SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        except OSError:
            log.exception("Couldn't write %s", SUMMARY_PATH)
