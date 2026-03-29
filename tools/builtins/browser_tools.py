"""Browser automation tools -- navigate and interact with web pages via Playwright."""

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

# Session state
_browser = None
_playwright = None
_sessions: dict[str, dict] = {}  # task_id -> {"page": Page, "last_activity": float}
SESSION_TIMEOUT = 300  # 5 minutes
_cleanup_task = None


async def _ensure_browser():
    """Lazy-init Playwright browser."""
    global _browser, _playwright
    if _browser is not None:
        return _browser

    try:
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        # Start cleanup task
        global _cleanup_task
        if _cleanup_task is None:
            _cleanup_task = asyncio.create_task(_cleanup_loop())
        return _browser
    except Exception as e:
        raise RuntimeError(f"Failed to start browser: {e}")


async def _get_page(task_id: str = "default"):
    """Get or create a page for the given task."""
    if task_id in _sessions:
        _sessions[task_id]["last_activity"] = time.time()
        return _sessions[task_id]["page"]

    browser = await _ensure_browser()
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent="Mozilla/5.0 (compatible; Kaigara/1.0)",
    )
    page = await context.new_page()
    _sessions[task_id] = {"page": page, "context": context, "last_activity": time.time()}
    return page


async def _cleanup_loop():
    """Periodically close stale browser sessions."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        stale = [k for k, v in _sessions.items() if now - v["last_activity"] > SESSION_TIMEOUT]
        for task_id in stale:
            try:
                session = _sessions.pop(task_id)
                await session["context"].close()
                log.info("Closed stale browser session: %s", task_id)
            except Exception:
                pass


async def browser_navigate(args: dict) -> str:
    url = args.get("url", "")
    if not url:
        return json.dumps({"error": "url is required"})
    task_id = args.get("task_id", "default")

    try:
        page = await _get_page(task_id)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        # Get a text snippet
        snippet = await page.evaluate("() => document.body?.innerText?.slice(0, 2000) || ''")
        return json.dumps({
            "title": title,
            "url": page.url,
            "status": response.status if response else None,
            "snippet": snippet,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_snapshot(args: dict) -> str:
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session -- use browser_navigate first"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()
        title = await page.title()
        url = page.url

        # Get accessibility tree as text
        content = await page.evaluate("""() => {
            function walk(node, depth = 0) {
                let result = '';
                const indent = '  '.repeat(depth);
                const role = node.getAttribute?.('role') || node.tagName?.toLowerCase() || '';
                const name = node.getAttribute?.('aria-label') || node.getAttribute?.('alt') || '';
                const text = node.nodeType === 3 ? node.textContent?.trim() : '';

                if (text) return indent + text + '\\n';
                if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(node.tagName)) return '';

                const tag = role || node.tagName?.toLowerCase() || '';
                const label = name ? ` "${name}"` : '';
                const href = node.getAttribute?.('href');
                const hrefStr = href ? ` href="${href}"` : '';

                if (tag && !['div', 'span', '#document', 'html', 'head'].includes(tag)) {
                    result += indent + `[${tag}${label}${hrefStr}]` + '\\n';
                }
                for (const child of (node.childNodes || [])) {
                    result += walk(child, depth + (tag ? 1 : 0));
                }
                return result;
            }
            return walk(document.body).slice(0, 8000);
        }""")

        return json.dumps({"title": title, "url": url, "content": content})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_click(args: dict) -> str:
    selector = args.get("selector", "")
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session"})
    if not selector:
        return json.dumps({"error": "selector is required"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()

        # Try CSS selector first, then text-based
        try:
            await page.click(selector, timeout=5000)
        except Exception:
            await page.get_by_text(selector, exact=False).first.click(timeout=5000)

        await page.wait_for_load_state("domcontentloaded", timeout=5000)
        return json.dumps({"status": "clicked", "url": page.url, "title": await page.title()})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_type(args: dict) -> str:
    selector = args.get("selector", "")
    text = args.get("text", "")
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()

        if selector:
            await page.fill(selector, text, timeout=5000)
        else:
            await page.keyboard.type(text)
        return json.dumps({"status": "typed", "text": text})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_scroll(args: dict) -> str:
    direction = args.get("direction", "down")
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()
        delta = 500 if direction == "down" else -500
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(0.3)
        position = await page.evaluate("() => ({ x: window.scrollX, y: window.scrollY })")
        return json.dumps({"status": "scrolled", "direction": direction, "position": position})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_back(args: dict) -> str:
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()
        await page.go_back(wait_until="domcontentloaded", timeout=10000)
        return json.dumps({"status": "navigated back", "url": page.url, "title": await page.title()})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_press(args: dict) -> str:
    key = args.get("key", "")
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"error": "no active browser session"})
    if not key:
        return json.dumps({"error": "key is required"})

    try:
        page = _sessions[task_id]["page"]
        _sessions[task_id]["last_activity"] = time.time()
        await page.keyboard.press(key)
        return json.dumps({"status": "pressed", "key": key})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def browser_close(args: dict) -> str:
    task_id = args.get("task_id", "default")
    if task_id not in _sessions:
        return json.dumps({"status": "no session to close"})

    try:
        session = _sessions.pop(task_id)
        await session["context"].close()
        return json.dumps({"status": "session closed"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _check_browser() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def register(registry):
    """Register all browser tools."""
    tools = [
        (
            "browser_navigate",
            "Navigate to a URL and return the page title and text snippet.",
            {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                    "task_id": {"type": "string", "default": "default"},
                },
                "required": ["url"],
            },
            browser_navigate,
        ),
        (
            "browser_snapshot",
            "Get the current page content as an accessibility tree.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "default": "default"},
                },
            },
            browser_snapshot,
        ),
        (
            "browser_click",
            "Click an element by CSS selector or visible text.",
            {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector or visible text",
                    },
                    "task_id": {"type": "string", "default": "default"},
                },
                "required": ["selector"],
            },
            browser_click,
        ),
        (
            "browser_type",
            "Type text into an input field.",
            {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input (optional if already focused)",
                    },
                    "text": {"type": "string", "description": "Text to type"},
                    "task_id": {"type": "string", "default": "default"},
                },
                "required": ["text"],
            },
            browser_type,
        ),
        (
            "browser_scroll",
            "Scroll the page up or down.",
            {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "default": "down",
                    },
                    "task_id": {"type": "string", "default": "default"},
                },
            },
            browser_scroll,
        ),
        (
            "browser_back",
            "Navigate back to the previous page.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "default": "default"},
                },
            },
            browser_back,
        ),
        (
            "browser_press",
            "Press a keyboard key (Enter, Tab, Escape, etc.).",
            {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Key to press (e.g. Enter, Tab, Escape)",
                    },
                    "task_id": {"type": "string", "default": "default"},
                },
                "required": ["key"],
            },
            browser_press,
        ),
        (
            "browser_close",
            "Close the browser session.",
            {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "default": "default"},
                },
            },
            browser_close,
        ),
    ]

    for name, desc, params, handler in tools:
        registry.register(
            name=name,
            description=desc,
            parameters=params,
            handler=handler,
            toolset="browser",
            check_fn=_check_browser,
        )
