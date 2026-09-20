"""Terminal output formatting for the MITRITY multi-agent governance demo."""

import asyncio
import os

from rich.console import Console
from rich.table import Table

console = Console()

# Counters for the summary.
stats = {"allowed": 0, "blocked": 0, "held": 0, "total_ms": 0}


def phase_header(number: int, title: str) -> None:
    """Print a phase header banner."""
    console.print()
    console.rule(f"[bold]Phase {number}: {title}[/bold]", style="cyan")
    console.print()


def scenario_header(label: str, title: str) -> None:
    """Print a scenario rule inside a phase (S1 ... S6)."""
    console.print()
    console.rule(f"[cyan]{label} — {title}[/cyan]", style="dim")


def tool_allowed(tool_name: str, detail: str, duration_ms: int = 0) -> None:
    """Log an allowed tool call."""
    stats["allowed"] += 1
    stats["total_ms"] += duration_ms
    ms = f"({duration_ms}ms)" if duration_ms else ""
    console.print(f"  [green]OK[/green] {tool_name} [dim]{ms}[/dim]")
    if detail:
        console.print(f"     [dim]{_truncate(detail, 120)}[/dim]")


def tool_blocked(tool_name: str, reason: str, duration_ms: int = 0) -> None:
    """Log a blocked tool call."""
    stats["blocked"] += 1
    stats["total_ms"] += duration_ms
    ms = f"({duration_ms}ms)" if duration_ms else ""
    console.print(f"  [red]BLOCKED[/red] {tool_name} [dim]{ms}[/dim]")
    if reason:
        console.print(f"     [dim]{_truncate(reason, 200)}[/dim]")


def tool_held(tool_name: str, detail: str, duration_ms: int = 0) -> None:
    """Log a held tool call (pending approval)."""
    stats["held"] += 1
    stats["total_ms"] += duration_ms
    ms = f"({duration_ms}ms)" if duration_ms else ""
    console.print(f"  [yellow]HELD[/yellow] {tool_name} [dim]{ms}[/dim]")
    if detail:
        console.print(f"     [dim]{_truncate(detail, 120)}[/dim]")


def worker_reply(worker: str, elapsed_sec: float, result: str) -> None:
    """Log what a worker answered on an allowed delegation (its own hops are on the dashboard)."""
    console.print(f"     [magenta]{worker}[/magenta] [dim]({elapsed_sec}s)[/dim] {_truncate(result, 300)}")


def agent_message(text: str) -> None:
    """Print the agent's reasoning / response."""
    console.print(f"  [blue]Agent:[/blue] {_truncate(text, 200)}")


def info(text: str) -> None:
    """Print an info message."""
    console.print(f"  [dim]{text}[/dim]")


def print_summary() -> None:
    """Print the final summary table."""
    cp_url = os.environ.get("MITRITY_CONTROL_PLANE_URL", "https://mitrity.com")
    # Strip the api. prefix for the dashboard URL.
    app_url = cp_url.replace("api.", "").rstrip("/") + "/app"

    console.print()
    console.rule("[bold]Summary[/bold]", style="cyan")
    console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Allowed", f"[green]{stats['allowed']}[/green]")
    table.add_row("Blocked", f"[red]{stats['blocked']}[/red]")
    table.add_row("Held", f"[yellow]{stats['held']}[/yellow]")
    table.add_row("", "")
    table.add_row("Delegation chains", f"[cyan]{app_url}/delegation-chains[/cyan]")
    table.add_row("Threat intel", f"[cyan]{app_url}/threat-intel[/cyan]")
    table.add_row("Audit trail", f"[cyan]{app_url}/audit[/cyan]")
    console.print(table)
    console.print()


async def pause(seconds: float = 1.0) -> None:
    """Pause between actions without blocking the event loop. Skipped in fast mode."""
    if os.environ.get("MITRITY_DEMO_SPEED") == "fast":
        return
    await asyncio.sleep(seconds)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, replacing newlines."""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text
