"""
infrastructure/container.py
Builds and exposes all shared singletons.
Import `container` anywhere you need a use case or manager.
"""
from adapters.repositories.sqlite_repositories import SqliteBotRepository, SqliteIncidentRepository
from notifications.manager import NotificationManager, LogChannel, WebhookChannel, EmailChannel
from use_cases.watchdog import ProcessHeartbeatUseCase, RunWatchdogUseCase
from infrastructure.config import settings


class Container:
    def __init__(self):
        # Repositories
        self.bot_repo = SqliteBotRepository()
        self.incident_repo = SqliteIncidentRepository()

        # Notification channels
        channels = [LogChannel()]
        if settings.WEBHOOK_URL:
            channels.append(WebhookChannel(settings.WEBHOOK_URL))
        if settings.EMAIL_RECIPIENTS:
            channels.append(EmailChannel(settings.EMAIL_RECIPIENTS))
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
