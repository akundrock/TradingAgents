from __future__ import annotations

import csv
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from tradingagents.intraday.gating import GatingLayer
from tradingagents.intraday.premarket_cache import (
    PremarketCache,
    analysts_match,
    cache_path,
    load_premarket_cache,
    merge_report,
    save_premarket_cache,
)
from tradingagents.intraday.mtf_validator import MultiTimeframeValidator
from tradingagents.intraday.session import (
    DailyBiasReport,
    IntradaySignal,
    SymbolScanState,
    TradingSession,
)
from tradingagents.intraday.strategies import get_strategy
from tradingagents.intraday.strategy import StrategyResult

if TYPE_CHECKING:
    from cli.intraday_display import IntradayDashboardBuffer
    from tradingagents.graph.intraday_graph import IntradayTradingGraph
    from tradingagents.graph.trading_graph import TradingAgentsGraph

logger = logging.getLogger(__name__)
console = Console()


class WatchlistScanner:
    def __init__(
        self,
        config: dict,
        ta_graph: TradingAgentsGraph,
        *,
        dry_run: bool = False,
        skip_premarket: bool = False,
        restore_premarket: bool = True,
        force_premarket: bool = False,
        dashboard: IntradayDashboardBuffer | None = None,
    ):
        self.config = config
        self.ta_graph = ta_graph
        self.dry_run = dry_run
        self.skip_premarket = skip_premarket
        self.restore_premarket = restore_premarket
        self.force_premarket = force_premarket
        self.dashboard = dashboard
        self.mtf_validator = MultiTimeframeValidator()
        self.gating = GatingLayer()
        self.strategy = get_strategy(str(config.get("intraday_strategy", "base_momentum")))
        self.intraday_graph: IntradayTradingGraph | None = None
        if not dry_run:
            from tradingagents.graph.intraday_graph import IntradayTradingGraph

            self.intraday_graph = IntradayTradingGraph(config)

        today = datetime.now().strftime("%Y-%m-%d")
        watchlist = list(config.get("watchlist") or [])
        self.session = TradingSession(session_date=today, watchlist=watchlist)
        self._output_dir = Path(
            str(config.get("intraday_output_dir", "~/.tradingagents/intraday"))
        ).expanduser()

    def run(self) -> TradingSession:
        if not self.session.watchlist:
            raise ValueError("Watchlist is empty. Provide symbols via --watchlist or config.")

        if self.dashboard is None:
            console.print(
                f"[bold]Intraday scanner[/bold] — {self.session.session_date} "
                f"strategy={self.strategy.name} symbols={', '.join(self.session.watchlist)}"
            )

        if self.skip_premarket:
            self._seed_neutral_bias()
        else:
            self._run_premarket_setup()

        self.session.status = "active"
        self._session_loop()
        self.session.status = "closed"
        self._eod_summary()
        return self.session

    def _seed_neutral_bias(self) -> None:
        logger.info("Pre-market setup skipped; seeding neutral bias for %d symbols", len(self.session.watchlist))
        now = datetime.now()
        for symbol in self.session.watchlist:
            self.session.daily_bias_cache[symbol] = DailyBiasReport(
                symbol=symbol,
                trade_date=self.session.session_date,
                direction="neutral",
                key_levels={},
                summary="Pre-market setup skipped; neutral bias applied.",
                computed_at=now,
            )

    def _run_premarket_setup(self) -> None:
        if self.dashboard is None:
            console.print("[cyan]Running pre-market daily bias setup...[/cyan]")
        analysts = list(self.config.get("intraday_premarket_analysts") or [])
        cache_file = cache_path(self._output_dir, self.session.session_date)
        disk_cache: PremarketCache | None = None
        if self.restore_premarket and not self.force_premarket:
            disk_cache = load_premarket_cache(cache_file, session_date=self.session.session_date)

        symbols_to_compute: list[str] = []
        restored = 0

        for symbol in self.session.watchlist:
            if self.force_premarket or not self.restore_premarket:
                symbols_to_compute.append(symbol)
                continue

            cached_report = None
            if disk_cache is not None and symbol in disk_cache.reports:
                if analysts_match(disk_cache.analysts, analysts):
                    cached_report = disk_cache.reports[symbol]
                else:
                    logger.info(
                        "Re-running pre-market for %s (analyst config changed)",
                        symbol,
                    )

            if cached_report is not None:
                self.session.daily_bias_cache[symbol] = cached_report
                restored += 1
                logger.info(
                    "Restored pre-market bias for %s: %s (cached %s)",
                    symbol,
                    cached_report.direction,
                    cached_report.computed_at.strftime("%H:%M"),
                )
                if self.dashboard is not None:
                    self.dashboard.load_premarket_from_bias(symbol, analysts, cached_report)
                else:
                    console.print(f"  {symbol}: {cached_report.direction} (restored)")
            else:
                if disk_cache is not None and symbol in disk_cache.reports:
                    pass  # already logged analyst change above
                elif disk_cache is not None:
                    logger.info("Running pre-market for %s (not in cache)", symbol)
                symbols_to_compute.append(symbol)

        if symbols_to_compute:
            logger.info(
                "Pre-market LLM setup for %d symbols: %s",
                len(symbols_to_compute),
                ", ".join(symbols_to_compute),
            )
            max_workers = int(self.config.get("intraday_max_concurrent_symbols", 5))

            def _bias_for_symbol(symbol: str) -> tuple[str, DailyBiasReport]:
                logger.info("Starting daily bias for %s", symbol)
                if self.dashboard is not None:
                    self.dashboard.init_premarket_symbol(symbol, analysts)

                    def _on_chunk(chunk: dict) -> None:
                        self.dashboard.update_premarket_chunk(symbol, chunk)

                    report = self.ta_graph.propagate_daily_bias(
                        symbol,
                        self.session.session_date,
                        on_chunk=_on_chunk,
                    )
                else:
                    report = self.ta_graph.propagate_daily_bias(symbol, self.session.session_date)
                logger.info("Completed daily bias for %s: %s", symbol, report.direction)
                return symbol, report

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_bias_for_symbol, symbol): symbol
                    for symbol in symbols_to_compute
                }
                for future in as_completed(futures):
                    symbol, report = future.result()
                    self.session.daily_bias_cache[symbol] = report
                    disk_cache = merge_report(
                        disk_cache,
                        session_date=self.session.session_date,
                        analysts=analysts,
                        symbol=symbol,
                        report=report,
                    )
                    if self.dashboard is None:
                        console.print(f"  {symbol}: {report.direction}")

            if disk_cache is not None:
                save_premarket_cache(cache_file, disk_cache)

        if self.dashboard is not None:
            self.dashboard.finish_premarket()

        computed = len(symbols_to_compute)
        logger.info(
            "Pre-market setup complete: %d restored, %d computed",
            restored,
            computed,
        )
        if self.dashboard is None:
            console.print(
                f"[cyan]Pre-market complete:[/cyan] {restored} restored, {computed} computed"
            )
        self.session.status = "pre_market"

    def _session_loop(self) -> None:
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError as exc:
            raise ImportError(
                'APScheduler is required for intraday scanning. '
                'Install with: pip install "tradingagents[intraday]"'
            ) from exc

        tz_name = str(self.config.get("intraday_timezone", "America/New_York"))
        interval = int(self.config.get("intraday_scan_interval_minutes", 5))
        start_str = str(self.config.get("intraday_session_start", "09:30"))
        end_str = str(self.config.get("intraday_session_end", "16:00"))
        start_hour, start_minute = [int(x) for x in start_str.split(":", maxsplit=1)]
        end_hour, end_minute = [int(x) for x in end_str.split(":", maxsplit=1)]

        scheduler = BlockingScheduler(timezone=tz_name)

        def _job() -> None:
            from zoneinfo import ZoneInfo

            bar_time = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
            self._on_bar_close(bar_time)

        trigger = CronTrigger(
            minute=f"*/{interval}",
            hour=f"{start_hour}-{end_hour}",
            timezone=tz_name,
        )
        scheduler.add_job(_job, trigger)

        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        logger.info(
            "Scheduler started (%s–%s %s, every %dm)",
            start_str,
            end_str,
            tz_name,
            interval,
        )
        next_scan = trigger.get_next_fire_time(None, now)
        if next_scan is not None:
            logger.info("Next scheduled scan: %s", next_scan)

        end_today = datetime.now().replace(
            hour=end_hour, minute=end_minute, second=0, microsecond=0
        )
        if datetime.now() < end_today:
            scheduler.add_job(scheduler.shutdown, "date", run_date=end_today)
            if self._within_session(now):
                logger.info("Running initial scan...")
                self._on_bar_close(now)
            scheduler.start()
        else:
            console.print("[yellow]Session end time already passed; running one scan.[/yellow]")
            logger.info("Session end passed; running one-off scan")
            self._on_bar_close(datetime.now())

    def _on_bar_close(self, bar_time: datetime) -> None:
        if not self._within_session(bar_time):
            logger.debug(
                "Skipping scan at %s: outside session window (%s–%s)",
                bar_time.strftime("%H:%M"),
                self.config.get("intraday_session_start", "09:30"),
                self.config.get("intraday_session_end", "16:00"),
            )
            return

        delay = int(self.config.get("intraday_bar_close_delay_seconds", 15))
        if delay > 0:
            logger.debug("Waiting %ds for bar-close data to settle", delay)
            time.sleep(delay)

        self.session.last_scan_time = bar_time
        self.session.scan_count += 1
        logger.info("Scan #%d @ %s", self.session.scan_count, bar_time.strftime("%H:%M"))
        max_workers = int(self.config.get("intraday_max_concurrent_symbols", 5))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._evaluate_symbol, symbol, bar_time): symbol
                for symbol in self.session.watchlist
            }
            for future in as_completed(futures):
                signal = future.result()
                if signal is not None:
                    self._emit_signal(signal)

        if self.dashboard is not None:
            self.dashboard._notify()

    def _within_session(self, bar_time: datetime) -> bool:
        start_str = str(self.config.get("intraday_session_start", "09:30"))
        end_str = str(self.config.get("intraday_session_end", "16:00"))
        start_hour, start_minute = [int(x) for x in start_str.split(":", maxsplit=1)]
        end_hour, end_minute = [int(x) for x in end_str.split(":", maxsplit=1)]
        start = bar_time.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end = bar_time.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        return start <= bar_time <= end

    def _evaluate_symbol(self, symbol: str, bar_time: datetime) -> IntradaySignal | None:
        try:
            mtf = self.mtf_validator.evaluate(symbol, bar_time, self.session, self.config)
        except Exception as exc:
            logger.warning("MTF validation failed for %s: %s", symbol, exc)
            return None

        daily_bias = self.session.daily_bias_cache.get(symbol)
        if daily_bias is None:
            logger.debug("%s: no daily bias cached", symbol)
            return None

        strategy_result = self.strategy.check_setup(symbol, mtf, daily_bias)
        gate_result = self.gating.evaluate(mtf, strategy_result, daily_bias, self.config)
        self._record_scan_state(
            symbol, bar_time, daily_bias, mtf, strategy_result, gate_result
        )
        self._record_gate_stats(gate_result)
        logger.info(
            "%s %s",
            symbol,
            self._gate_log_line(
                gate_result, strategy_result, self.strategy.name, daily_bias.direction
            ),
        )

        if not gate_result.passed:
            return None

        if self._is_duplicate_signal(symbol, gate_result.final_direction, bar_time):
            logger.debug(
                "%s: duplicate %s signal within cooldown window",
                symbol,
                gate_result.final_direction,
            )
            return None

        if self.dry_run:
            return self._build_dry_run_signal(symbol, bar_time, strategy_result, gate_result)

        if self.intraday_graph is None:
            return None

        return self.intraday_graph.propagate_intraday(
            symbol=symbol,
            bar_time=bar_time,
            daily_bias=daily_bias,
            mtf=mtf,
            strategy_result=strategy_result,
            gate_result=gate_result,
            strategy_name=self.strategy.name,
        )

    def _is_duplicate_signal(self, symbol: str, direction: str, bar_time: datetime) -> bool:
        last = self.session.last_signal_by_symbol.get(symbol)
        if last is None:
            return False
        cooldown_bars = int(self.config.get("intraday_signal_cooldown_bars", 3))
        interval = int(self.config.get("intraday_scan_interval_minutes", 5))
        cooldown = timedelta(minutes=cooldown_bars * interval)
        same_direction = (
            (direction == "long" and last.direction == "long")
            or (direction == "short" and last.direction == "short")
        )
        return same_direction and (bar_time - last.bar_time) < cooldown

    def _build_dry_run_signal(
        self,
        symbol: str,
        bar_time: datetime,
        strategy_result: StrategyResult,
        gate_result,
    ) -> IntradaySignal:
        action = "BUY" if gate_result.final_direction == "long" else "SELL"
        return IntradaySignal(
            symbol=symbol,
            bar_time=bar_time,
            action=action,
            direction=gate_result.final_direction,
            entry_price=None,
            stop_loss=None,
            confidence=self._strategy_confidence(strategy_result),
            setup_score=len(strategy_result.factors_met),
            gate_summary=self._gate_summary(gate_result),
            reasoning="Dry-run: LLM pipeline skipped.",
        )

    @staticmethod
    def _strategy_confidence(strategy_result: StrategyResult) -> str:
        if strategy_result.passed and not strategy_result.factors_missing:
            return "Strong"
        if strategy_result.factors_met:
            return "Marginal"
        return "None"

    def _record_scan_state(
        self,
        symbol: str,
        bar_time: datetime,
        daily_bias: DailyBiasReport,
        mtf,
        strategy_result: StrategyResult,
        gate_result,
    ) -> None:
        indicator_snapshot = None
        indicator_fn = getattr(self.strategy, "indicator_snapshot", None)
        if callable(indicator_fn):
            try:
                indicator_snapshot = indicator_fn(symbol, mtf, daily_bias, self.config)
            except Exception:
                indicator_snapshot = None

        scan_state = SymbolScanState(
            symbol=symbol,
            bar_time=bar_time,
            daily_bias_direction=daily_bias.direction,
            strategy_name=self.strategy.name,
            strategy_direction=strategy_result.direction,
            factors_met=list(strategy_result.factors_met),
            factors_missing=list(strategy_result.factors_missing),
            gate1_passed=gate_result.gate1_strategy,
            gate2_passed=gate_result.gate2_mtf_alignment,
            gate_passed=gate_result.passed,
            final_direction=gate_result.final_direction if gate_result.passed else None,
            setup_score=len(strategy_result.factors_met),
            indicator_snapshot=indicator_snapshot,
        )
        self.session.latest_scan_by_symbol[symbol] = scan_state
        if self.dashboard is not None:
            self.dashboard.record_scan_state(scan_state)

    def _record_gate_stats(self, gate_result) -> None:
        if gate_result.passed:
            self.session.gate_stats["all_pass"] += 1
        elif not gate_result.gate1_strategy:
            self.session.gate_stats["gate1_fail"] += 1
        else:
            self.session.gate_stats["gate2_fail"] += 1

    def _emit_signal(self, signal: IntradaySignal) -> None:
        self.session.signal_log.append(signal)
        self.session.last_signal_by_symbol[signal.symbol] = signal

        if self.dashboard is not None:
            self.dashboard.add_signal(signal)
        else:
            console.print(
                f"[green]SIGNAL[/green] {signal.symbol} {signal.action} "
                f"@ {signal.bar_time:%H:%M} — {signal.reasoning[:120]}"
            )

        out_dir = self._output_dir / self.session.session_date
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "signals.csv"
        write_header = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "bar_time",
                    "symbol",
                    "action",
                    "direction",
                    "entry_price",
                    "stop_loss",
                    "confidence",
                    "setup_score",
                    "gate_summary",
                    "reasoning",
                ],
            )
            if write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "bar_time": signal.bar_time.isoformat(),
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "direction": signal.direction,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "confidence": signal.confidence,
                    "setup_score": signal.setup_score,
                    "gate_summary": signal.gate_summary,
                    "reasoning": signal.reasoning,
                }
            )

    @staticmethod
    def _gate_summary(gate_result) -> str:
        return (
            f"G1={gate_result.gate1_strategy} G2={gate_result.gate2_mtf_alignment}"
        )

    @staticmethod
    def _gate_log_line(
        gate_result,
        strategy_result: StrategyResult,
        strategy_name: str,
        daily_bias_direction: str,
    ) -> str:
        factors_met = ",".join(strategy_result.factors_met) or "none"

        if gate_result.passed:
            return (
                f"gates=PASS direction={gate_result.final_direction} "
                f"strategy={strategy_name} daily_bias={daily_bias_direction} "
                f"factors_met=[{factors_met}] "
                f"({WatchlistScanner._gate_summary(gate_result)})"
            )
        if not gate_result.gate1_strategy:
            return (
                f"gates=G1=fail strategy={strategy_name} daily_bias={daily_bias_direction} "
                f"blocked: {gate_result.gate1_reason}"
            )
        return (
            f"gates=G2=fail strategy={strategy_name} daily_bias={daily_bias_direction} "
            f"direction={gate_result.final_direction} {gate_result.gate2_reason}"
        )

    def _eod_summary(self) -> None:
        if self.dashboard is not None:
            return
        table = Table(title="Intraday Session Summary")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Scans", str(self.session.scan_count))
        table.add_row("Signals", str(len(self.session.signal_log)))
        for key, value in self.session.gate_stats.items():
            table.add_row(key, str(value))
        console.print(table)
