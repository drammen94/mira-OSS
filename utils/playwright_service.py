"""
Security-hardened Playwright service for rendering JavaScript-heavy webpages.

Provides a singleton headless browser that executes JavaScript and returns
fully-rendered HTML. Implements security controls to prevent SSRF, resource
exhaustion, and other browser-based attacks.
"""
import logging
import re
import threading
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


class PlaywrightService:
    """
    Singleton headless browser service for JavaScript-rendered webpages.

    This service manages a single Chromium browser process shared across all users.
    Browser contexts provide user isolation while sharing the underlying browser.
    Security controls prevent SSRF attacks and resource exhaustion.
    """

    _instance: Optional['PlaywrightService'] = None
    _lock = threading.Lock()

    def __init__(self):
        """Initialize the Playwright service with security hardening."""
        self.logger = logging.getLogger("playwright_service")

        # Blocked network patterns for SSRF prevention
        self._blocked_patterns = [
            r'^https?://localhost',
            r'^https?://127\.',
            r'^https?://10\.',
            r'^https?://172\.(1[6-9]|2[0-9]|3[0-1])\.',
            r'^https?://192\.168\.',
            r'^https?://0\.0\.0\.0',
            r'^https?://\[::1\]',  # IPv6 localhost
            r'^https?://169\.254\.',  # Link-local
        ]

        # Initialize Playwright in a separate thread to avoid asyncio conflicts
        try:
            import concurrent.futures
            import psutil
            import time

            def _init_playwright():
                """Run Playwright initialization outside the asyncio event loop."""
                # Capture Chromium processes before launch for PID tracking
                chromium_before = {p.pid for p in psutil.process_iter()
                                  if 'chromium' in p.name().lower()}

                pw = sync_playwright().start()
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-gpu',
                        '--disable-dev-shm-usage',
                        '--disable-software-rasterizer',
                        '--disable-extensions',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                    ]
                )

                # Give browser time to fully start
                time.sleep(0.5)

                # Find the main browser process PID by comparing before/after
                chromium_after = {p.pid for p in psutil.process_iter()
                                 if 'chromium' in p.name().lower()}
                new_pids = chromium_after - chromium_before
                browser_pid = min(new_pids) if new_pids else None

                return pw, browser, browser_pid

            # Run initialization in thread pool to avoid asyncio event loop conflict
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_init_playwright)
                self.playwright, self.browser, browser_pid = future.result(timeout=30)
                self._browser_pid = browser_pid

            if self._browser_pid:
                self.logger.info(f"Chromium browser process {self._browser_pid} captured for forced shutdown")
            else:
                self.logger.warning("Could not capture Chromium process PID - force shutdown may not work")

            self.semaphore = threading.Semaphore(3)  # Max 3 concurrent contexts
            self.logger.info("PlaywrightService initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Playwright: {e}")
            raise RuntimeError(f"Playwright initialization failed: {e}") from e

    @classmethod
    def get_instance(cls) -> 'PlaywrightService':
        """
        Get or create the singleton PlaywrightService instance.

        Returns:
            PlaywrightService singleton instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def fetch_rendered_html(
        self,
        url: str,
        timeout: int = 30,
        max_size_mb: int = 10
    ) -> str:
        """
        Fetch fully-rendered HTML after JavaScript execution.

        Args:
            url: Target URL (must already be validated by caller)
            timeout: Max time for page load in seconds
            max_size_mb: Max response size to prevent memory bombs

        Returns:
            Rendered HTML content as string

        Raises:
            TimeoutError: If page load exceeds timeout
            RuntimeError: For other failures (HTTP errors, size limits, etc.)
        """
        with self.semaphore:
            context = self.browser.new_context(
                # Disable risky browser features
                geolocation=None,
                permissions=[],
                bypass_csp=False,
                java_script_enabled=True,

                # Performance/security limits
                viewport={'width': 1280, 'height': 720},
                ignore_https_errors=False,

                # User agent
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0'
            )

            blocked_requests = []

            def handle_request(route, request):
                """Intercept and validate all network requests."""
                request_url = request.url

                # Block internal network requests (SSRF protection)
                for pattern in self._blocked_patterns:
                    if re.match(pattern, request_url, re.IGNORECASE):
                        self.logger.warning(f"Blocked SSRF attempt: {request_url}")
                        blocked_requests.append(request_url)
                        route.abort()
                        return

                # Block unnecessary resource types for faster loading
                resource_type = request.resource_type
                if resource_type in ['font', 'media', 'websocket']:
                    route.abort()
                    return

                # Continue with request
                route.continue_()

            try:
                page = context.new_page()

                # Set up request interception
                page.route('**/*', handle_request)

                # Set resource limits
                page.set_default_timeout(timeout * 1000)
                page.set_default_navigation_timeout(timeout * 1000)

                # Navigate with strict timeout
                try:
                    response = page.goto(
                        url,
                        wait_until='networkidle',
                        timeout=timeout * 1000
                    )

                    # Additional wait for dynamic content (Angular/React apps often need this)
                    # Wait 2 seconds for lazy-loaded content and API calls
                    self.logger.debug("Waiting 2s for dynamic content to load...")
                    page.wait_for_timeout(2000)

                    # Wait for accordion content to populate (Angular/React data binding)
                    try:
                        # Wait up to 10s for accordion items to appear
                        self.logger.debug("Waiting for accordion/menu content to populate...")
                        page.wait_for_selector('.accordion-item, .accordion-button, [class*="menu-item"], [class*="category"]', timeout=10000)
                        self.logger.debug("Accordion content detected")
                        page.wait_for_timeout(500)  # Let Angular finish rendering
                    except Exception:
                        self.logger.warning("No accordion content appeared - page may not have dynamic menus")

                    # Progressive scroll + expand strategy for lazy-loaded accordion content
                    self.logger.debug("Progressive scroll with drawer expansion...")

                    # Expand script that we'll run at each scroll position
                    expand_script = """
                    (() => {
                        const selectors = [
                            '[class*="accordion"]', '[class*="collapse"]', '[class*="expand"]',
                            '[class*="drawer"]', '[aria-expanded="false"]', '[role="button"]',
                            'button[class*="toggle"]', '.mat-expansion-panel-header',
                            '[data-toggle="collapse"]'
                        ];

                        let clickCount = 0;
                        for (const selector of selectors) {
                            const elements = document.querySelectorAll(selector);
                            for (const elem of elements) {
                                if (elem.getAttribute('aria-expanded') === 'true') continue;
                                // Check if element is visible in viewport
                                const rect = elem.getBoundingClientRect();
                                if (rect.top >= 0 && rect.top <= window.innerHeight) {
                                    try {
                                        elem.click();
                                        clickCount++;
                                    } catch (e) {}
                                }
                            }
                        }
                        return clickCount;
                    })()
                    """

                    # Scroll in increments, expanding at each position
                    scroll_height = page.evaluate("document.body.scrollHeight")
                    viewport_height = page.evaluate("window.innerHeight")
                    current_position = 0
                    total_clicks = 0

                    while current_position < scroll_height:
                        # Expand visible drawers at current scroll position
                        clicks = page.evaluate(expand_script)
                        total_clicks += clicks
                        if clicks > 0:
                            page.wait_for_timeout(800)  # Wait for expand animation

                        # Scroll down one viewport
                        current_position += viewport_height
                        page.evaluate(f"window.scrollTo(0, {current_position})")
                        page.wait_for_timeout(500)  # Wait for scroll-triggered content

                        # Update scroll height (page may have grown from expansions)
                        scroll_height = page.evaluate("document.body.scrollHeight")

                    self.logger.debug(f"Expanded {total_clicks} drawers across entire page")

                    # Scroll back to top to capture everything
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(300)

                except PlaywrightTimeoutError:
                    # Fallback: if networkidle times out, try domcontentloaded
                    self.logger.warning(f"Network idle timeout, falling back to domcontentloaded")
                    response = page.goto(
                        url,
                        wait_until='domcontentloaded',
                        timeout=timeout * 1000
                    )
                    # Still wait for dynamic content
                    page.wait_for_timeout(2000)

                    # Wait for accordion content
                    try:
                        page.wait_for_selector('.accordion-item, .accordion-button, [class*="menu-item"], [class*="category"]', timeout=10000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass

                    # Progressive scroll + expand (same as above)
                    expand_script = """
                    (() => {
                        const selectors = ['[class*="accordion"]', '[class*="collapse"]', '[class*="expand"]', '[class*="drawer"]', '[aria-expanded="false"]', '[role="button"]', 'button[class*="toggle"]', '.mat-expansion-panel-header', '[data-toggle="collapse"]'];
                        let clickCount = 0;
                        for (const selector of selectors) {
                            const elements = document.querySelectorAll(selector);
                            for (const elem of elements) {
                                if (elem.getAttribute('aria-expanded') === 'true') continue;
                                const rect = elem.getBoundingClientRect();
                                if (rect.top >= 0 && rect.top <= window.innerHeight) {
                                    try { elem.click(); clickCount++; } catch (e) {}
                                }
                            }
                        }
                        return clickCount;
                    })()
                    """
                    scroll_height = page.evaluate("document.body.scrollHeight")
                    viewport_height = page.evaluate("window.innerHeight")
                    current_position = 0
                    while current_position < scroll_height:
                        clicks = page.evaluate(expand_script)
                        if clicks > 0:
                            page.wait_for_timeout(800)
                        current_position += viewport_height
                        page.evaluate(f"window.scrollTo(0, {current_position})")
                        page.wait_for_timeout(500)
                        scroll_height = page.evaluate("document.body.scrollHeight")
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(300)

                # Check response status
                if response and response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")

                # Get rendered HTML
                html = page.content()

                # Check size limits
                html_size_mb = len(html) / (1024 * 1024)
                if html_size_mb > max_size_mb:
                    raise RuntimeError(
                        f"Content too large: {html_size_mb:.1f}MB exceeds {max_size_mb}MB limit"
                    )

                # Warn if internal requests were blocked
                if blocked_requests:
                    self.logger.warning(
                        f"Blocked {len(blocked_requests)} SSRF attempts from {url}"
                    )

                return html

            except PlaywrightTimeoutError:
                self.logger.error(f"Page load timeout after {timeout}s for {url}")
                raise TimeoutError(f"Page load timeout after {timeout}s")
            except Exception as e:
                self.logger.error(f"Failed to fetch {url}: {e}")
                raise
            finally:
                try:
                    context.close()
                except Exception as e:
                    self.logger.warning(f"Error closing context: {e}")

    def shutdown(self):
        """Force-kill the Chromium browser process."""
        try:
            if hasattr(self, '_browser_pid') and self._browser_pid:
                import psutil
                process = psutil.Process(self._browser_pid)
                process.kill()
                self.logger.info(f"Force-killed Chromium process {self._browser_pid}")
            else:
                self.logger.warning("No browser PID available for shutdown")
        except psutil.NoSuchProcess:
            self.logger.info("Chromium process already terminated")
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
