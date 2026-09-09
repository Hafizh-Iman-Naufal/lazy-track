from lazytrack import __version__

CHAT_BANNER = r"""
 _                       _____                _
| |    __ _ _____ _   _|_   _| __ __ _  ___| | __
| |   / _` |_  / | | | | | || '__/ _` |/ __| |/ /
| |__| (_| |/ /| |_| | | || | | (_| | (__|   <
|_____\__,_/___|\__, | |_||_|  \__,_|\___|_|\_\
                |___/
""".strip("\n")


def print_chat_banner(console) -> None:
    console.print(f"[bold cyan]{CHAT_BANNER}[/bold cyan]", highlight=False)
    console.print(f"         [dim]chat  v{__version__}[/dim]", highlight=False)
    console.print("[dim]  type /help or exit[/dim]", highlight=False)
