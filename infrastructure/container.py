"""
infrastructure/container.py
Builds and exposes all shared singletons.
Import `container` anywhere you need a use case or manager.
"""
from adapters.repositories.sqlite_repositories import SqliteBotRepository, SqliteIncidentRepository
from notifications.manager import NotificationManager, LogChannel, WebhookChannel, SendGridEmailChannel
from notifications.throttler import AlertThrottler
from use_cases.watchdog import ProcessHeartbeatUseCase, RunWatchdogUseCase
from infrastructure.config import settings


class Container:
    def __init__(self):
        # Repositories
        self.bot_repo = SqliteBotRepository()
        self.incident_repo = SqliteIncidentRepository()

        # Real alert channels — email is primary, Slack/webhook secondary.
        alert_channels = []
        if settings.SENDGRID_API_KEY and settings.ALERT_EMAIL_SENDER and settings.EMAIL_RECIPIENTS:
            alert_channels.append(SendGridEmailChannel(
                api_key=settings.SENDGRID_API_KEY,
                sender=settings.ALERT_EMAIL_SENDER,
                recipients=settings.EMAIL_RECIPIENTS,
            ))
        if settings.WEBHOOK_URL:
            alert_channels.append(WebhookChannel(settings.WEBHOOK_URL))

        # LogChannel always fires immediately; real alerts go through the
        # throttler (debounce + cooldown) so glitches/flaps don't page anyone.
        channels = [LogChannel()]
        if alert_channels:
            channels.append(AlertThrottler(
                channels=alert_channels,
                bot_repo=self.bot_repo,
                confirm_seconds=settings.ALERT_CONFIRM_SECONDS,
                cooldown_seconds=settings.ALERT_COOLDOWN_SECONDS,
            ))
        self.notification_manager = NotificationManager(channels)

        # Use cases
        self.process_heartbeat = ProcessHeartbeatUseCase(
            bot_repo=self.bot_repo,
            incident_repo=self.incident_repo,
            notification_manager=self.notification_manager,
        )
        self.run_watchdog = RunWatchdogUseCase(
            bot_repo=self.bot_repo,
            incident_repo=self.incident_repo,
            notification_manager=self.notification_manager,
            timeout_seconds=settings.HEARTBEAT_TIMEOUT_SECONDS,
            grace_seconds=settings.GRACE_PERIOD_SECONDS,
        )


container = Container()
