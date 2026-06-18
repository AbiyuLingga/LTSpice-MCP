# LTspice setup

This document explains how to get LTspice working on a host so that
`ltagent doctor --simulate` can run a batch simulation. It is the
companion to [`runner_troubleshooting.md`](runner_troubleshooting.md),
which explains what to do when this setup breaks.

## Supported configurations

- **Linux + Wine 10+ + LTspice XVII** (or newer). Tested on this host
  with `wine-11.0` and `LTspiceXVII`.
- **Windows 10/11 + LTspice native** (planned; not exercised in Phase 0
  because the development host is Linux).
- Other configurations (macOS, BSD, headless servers) are not
  officially supported.

## Linux + Wine + LTspice XVII

### 1. Install Wine

Stable Wine is recommended. On Debian / Ubuntu:

```bash
sudo dpkg --add-architecture i386
sudo mkdir -pm755 /etc/apt/keyrings
sudo wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
sudo wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/ubuntu/dists/$(lsb_release -sc)/winehq-$(lsb_release -sc).sources
sudo apt update
sudo apt install --install-recommends winehq-stable
```

Verify:

```bash
/opt/wine-stable/bin/wine --version
which wine      # may be empty; that is fine
```

> **Note:** Wine is often **not on the default `PATH`**. `ltagent`
> auto-detects `/opt/wine-stable/bin/wine` and the output of
> `which wine`. If you install Wine to a different prefix, set
> `ltspice.wine_command` in `config.toml` to its absolute path.

### 2. Install LTspice XVII inside the Wine prefix

Download the Windows installer from Analog Devices and run it under
Wine:

```bash
wine ~/Downloads/LTspiceXVII.exe
```

The default install path is
`C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe`, which under Wine lives
at `~/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe`
(mind the space in `Program Files`).

Verify the executable exists:

```bash
ls -l "$HOME/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe"
```

### 3. Configure `ltagent`

Copy the example config and edit it:

```bash
mkdir -p ~/.config/ltagent
cp config.example.toml ~/.config/ltagent/config.toml
$EDITOR ~/.config/ltagent/config.toml
```

Set the following keys to match your install:

```toml
[ltspice]
mode = "wine"
executable = "/home/<you>/.wine/drive_c/Program Files/LTC/LTspiceXVII/XVIIx64.exe"
wine_command = "/opt/wine-stable/bin/wine"
working_dir = "/home/<you>/Documents/LTspiceXVII"
```

### 4. Probe with `ltagent doctor`

```bash
ltagent doctor --json
```

If everything is in order, every check should be `ok`. If the smoke
simulation is wanted (slow):

```bash
ltagent doctor --simulate --json
```

If the doctor reports a Wine or LTspice issue, see
[`runner_troubleshooting.md`](runner_troubleshooting.md).

## Windows + LTspice native (planned)

```toml
[ltspice]
mode = "native"
executable = "C:\\Program Files\\LTC\\LTspiceXVII\\XVIIx64.exe"
wine_command = null
working_dir = "C:\\Users\\<you>\\Documents\\LTspiceXVII"
```

Native mode is not exercised by Phase 0 tests on this host.

## Security

The runner never invokes a shell. It always passes an argv list to
`subprocess.run` and the executable path is resolved against the
config — never against user-provided strings. See
[`security.md`](security.md) for the full model.
