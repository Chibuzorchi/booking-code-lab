"""Base for Playwright page objects — shared page handle + logging."""
from __future__ import annotations

from playwright.sync_api import Page

from engine.logger import get_logger
from infra.config.settings import Settings


class BasePage:
    def __init__(self, page: Page, settings: Settings, logger=None):
        self.page = page
        self.settings = settings
        self.logger = logger or get_logger(self.__class__.__name__)
