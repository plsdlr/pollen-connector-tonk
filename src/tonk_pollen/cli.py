"""The pollen command.

    pollen setup --space NAME     which tonk space this machine's agents write to
    pollen enable [DIR]           opt a project in: register the Claude Code hooks
    pollen disable [DIR]          opt it out again
    pollen status [DIR]           check machine, space and project; print the garden URL
    pollen hook claude            (run by Claude Code, not by hand) one hook event on stdin
    pollen version

DIR defaults to the current directory.

Imports are deferred to the subcommand: `pollen hook` runs on every tool call
and must stay as cheap as the adapter itself.
"""
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])

    if cmd == "hook":
        # Never fail and never print: a garden must not get in the way of the work.
        if rest == ["claude"]:
            from .adapters import claude
            claude.run()
        return 0
    if cmd == "status":
        from . import status
        return status.status(_dir(rest))
    if cmd == "setup":
        from . import install
        if len(rest) != 2 or rest[0] != "--space":
            sys.exit("usage: pollen setup --space NAME")
        return install.setup(rest[1])
    if cmd in ("enable", "disable"):
        from . import install
        return getattr(install, cmd)(_dir(rest))
    if cmd in ("version", "--version"):
        from . import __version__
        print(__version__)
        return 0
    sys.exit(__doc__.strip())


def _dir(rest):
    import os
    return os.path.abspath(rest[0] if rest else os.getcwd())


if __name__ == "__main__":
    sys.exit(main())
