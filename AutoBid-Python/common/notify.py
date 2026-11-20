"""Tell the user when the bot needs them: Windows notification + sound + console.

No extra packages: the notification is shown through Windows PowerShell.
"""
import os
import subprocess
import sys
import threading

# Set to False (e.g. notify.NOTIFY_SOUND = False in a bot's config) for no beep.
NOTIFY_SOUND = True

_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:AB_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:AB_MSG)) > $null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


def _toast(title, message):
    if sys.platform != "win32":
        return
    env = dict(os.environ, AB_TITLE=title, AB_MSG=message)   # passed as env: no quoting issues
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            env=env, timeout=15, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _beep():
    if not NOTIFY_SOUND:
        return
    try:
        import winsound
        for freq in (880, 1175, 880):
            winsound.Beep(freq, 180)
    except Exception:
        print("\a", end="", flush=True)


def notify(title, message):
    """Console line + Windows notification + short sound. Does not block."""
    print(f"\n>>> {title}: {message}", flush=True)
    threading.Thread(target=_toast, args=(title, message), daemon=True).start()
    threading.Thread(target=_beep, daemon=True).start()


if __name__ == "__main__":
    notify("AutoBid: bot check", "Test notification from the Ashbyhq bot")
    import time
    time.sleep(3)
