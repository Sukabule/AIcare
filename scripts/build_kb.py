"""Compatibility entry for kb_cli build."""

if __package__:
    from .kb_cli import main
else:
    from kb_cli import main

if __name__ == "__main__":
    main("build")
