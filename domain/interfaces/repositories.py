"""
domain/interfaces/repositories.py
Abstract ports — the contract, not the implementation.
"""
from abc import ABC, abstractmethod
from typing import List, Optional, Set, Tuple
from domain.entities.bot import Bot, Incident


class IBotRepository(ABC):

    @abstractmethod
    async def upsert(self, bot: Bot) -> Bot: ...

    @abstractmethod
    async def find_by_id(self, bot_id: str, environment: str) -> Optional[Bot]: ...

    @abstractmethod
    async def find_all(self) -> List[Bot]: ...

    @abstractmethod
    async def update_status(self, bot_id: str, environment: str, status: str) -> None: ...


class IIncidentRepository(ABC):

    @abstractmethod
    async def open_incident(self, incident: Incident) -> Incident: ...

    @abstractmethod
    async def close_incident(self, bot_id: str, environment: str, recovered_at) -> Optional[Incident]: ...

    @abstractmethod
    async def find_active_incident(self, bot_id: str, environment: str) -> Optional[Incident]: ...

    @abstractmethod
    async def find_active_bot_keys(self) -> Set[Tuple[str, str]]:
        """All (bot_id, environment) pairs with an open incident — one query.

        Lets the watchdog sweep skip the per-bot lookup (N+1) and decide in
        memory whether to open a new incident.
        """
        ...

    @abstractmethod
    async def find_all(self, limit: int = 100) -> List[Incident]: ...
