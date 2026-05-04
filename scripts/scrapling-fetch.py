#!/usr/bin/env python3
"""
scrapling-fetch v2 — Hermes Agent web fetcher.
All Scrapling features exposed as composable --flags.
Output: structured JSON. Agent uses output to decide next step.

USAGE (AI agents read this for tool calling):
  ctx_execute(shell,
    "python3 /path/to/scripts/scrapling-fetch.py --url URL [--stealth] [--select 'css'] [--raw] [--extract-links] [--compact] [--index-source 'label'] [--timeout 30] [--impersonate chrome] [--output-file /tmp/page.md] [--no-proxy] [--no-headless]")

IF FAILS: fall through to CLI:
  ctx_execute(shell,
    "scrapling extract get 'URL' /tmp/fetch.md --proxy '$PROXY' --impersonate chrome --ai-targeted && cat /tmp/fetch.md")

THEN:
  ctx_index(content=<output>, source=<label>)
  ctx_search(queries=["term"], source=<label>)
"""

import argparse, json, os, re, sys, time
from urllib.parse import urlparse

try:
    from scrapling.fetchers import Fetcher, StealthyFetcher
    # Suppress Scrapling's INFO logging (stderr). JSON stdout only.
    import logging
    logging.getLogger("scrapling").setLevel(logging.WARNING)
except ImportError:
    print(json.dumps({"status":"error","error":"scrapling not installed. Run: pip install scrapling[all] && scrapling install","action":"pip install scrapling[all] && scrapling install"}))
    sys.exit(1)

DEFAULT_PROXY = os.getenv("HH_PROXY", "")  # Set HH_PROXY in ~/.hermes/.env
# Example: HH_PROXY="http://USER:PASS@p.webshare.io:80"
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


def html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown. Preserves structure, links, headers."""
    html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<h([1-6])[^>]*>(.*?)</h\1>', lambda m: '\n'+'#'*int(m.group(1))+' '+m.group(2).strip()+'\n', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<a[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>', r'**\1**', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<(?:i|em)[^>]*>(.*?)</(?:i|em)>', r'*\1*', html, flags=re.IGNORECASE|re.DOTALL)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = html.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&quot;','"').replace('&#39;',"'").replace('&nbsp;',' ')
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def check_scrapling_cli() -> bool:
    """Check if scrapling CLI is available as fallback."""
    import subprocess
    try:
        subprocess.run(["scrapling", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def extract_with_scrapling(args) -> dict:
    """Fetch URL with Scrapling, extract content, return structured result."""
    
    proxy = args.proxy if args.proxy else (None if args.no_proxy else DEFAULT_PROXY)
    headers = {"User-Agent": DEFAULT_UA}
    if args.headers:
        for h in args.headers:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    
    start = time.time()
    fetcher_used = "unknown"
    
    try:
        if args.stealth:
            page = StealthyFetcher.fetch(
                args.url,
                headless=not args.no_headless,
                network_idle=args.network_idle,
                proxy=proxy,
                timeout=args.timeout * 1000 if args.timeout else 30000,
            )
            fetcher_used = "StealthyFetcher"
        else:
            page = Fetcher.get(
                args.url,
                proxy=proxy,
                headers=headers,
                timeout=args.timeout,
            )
            fetcher_used = "Fetcher"
        
        elapsed = round(time.time() - start, 3)
        title = page.css('title::text').get('') or ''
        h1 = page.css('h1::text').get('') or ''
        meta_desc = page.css('meta[name="description"]::attr(content)').get('') or ''
        
        # Content extraction
        if args.select:
            elements = page.css(args.select)
            parts = []
            for el in elements:
                text = el.text.strip() if hasattr(el, 'text') else str(el).strip()
                if text:
                    parts.append(text)
            body_text = '\n\n'.join(parts)
        elif args.raw:
            body_text = page.body.decode('utf-8') if isinstance(page.body, bytes) else str(page.body)
        elif args.text_only:
            # Text only — strip ALL tags
            body_html = page.css('body').get('') or str(page.body)
            body_text = re.sub(r'<[^>]+>', ' ', body_html)
            body_text = re.sub(r'[ \t\n\r]+', ' ', body_text).strip()
        else:
            body_html = page.css('body').get('') or ''
            if not body_html and hasattr(page, 'body'):
                body_html = page.body.decode('utf-8') if isinstance(page.body, bytes) else str(page.body)
            body_text = html_to_markdown(body_html)
        
        # Links
        links = []
        if args.extract_links:
            for link in page.css('a[href]')[:args.max_links]:
                href = link.attrib.get('href', '')
                text = link.text.strip() if hasattr(link, 'text') else ''
                if href and not href.startswith('#'):
                    links.append({"href": href, "text": text[:120]})
        
        result = {
            "status": "ok",
            "url": args.url,
            "fetcher": fetcher_used,
            "http_status": getattr(page, 'status', 200),
            "time_seconds": elapsed,
            "title": title or h1 or '',
            "h1": h1 or '',
            "meta_description": meta_desc,
            "bytes_fetched": len(body_text.encode('utf-8')),
            "chars": len(body_text),
            "token_estimate": len(body_text) // 4,
            "content_type": "html" if args.raw else ("text" if args.text_only else "markdown"),
            "content": body_text,
        }
        
        if args.extract_links:
            result["links"] = links
            result["links_count"] = len(links)
        
        # Write to file if output-file set
        if args.output_file:
            with open(args.output_file, 'w') as f:
                f.write(body_text)
            result["output_file"] = args.output_file
        
        # Index hint for agent
        if args.index_source:
            if args.compact:
                del result["content"]
                result["action"] = "call_ctx_index"
            else:
                result["action"] = "optional_ctx_index"
            result["index_hint"] = f"ctx_index(content=<above>, source='{args.index_source}')"
            result["indexed"] = False
        else:
            result["action"] = "content_in_context"
        
        return result
    
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        has_cli = check_scrapling_cli()
        return {
            "status": "error",
            "url": args.url,
            "fetcher": fetcher_used,
            "time_seconds": elapsed,
            "error": str(e),
            "error_type": type(e).__name__,
            "fallback_available": has_cli,
            "fallback_cmd": "scrapling extract get '" + args.url + "' /tmp/fetch.md --proxy '$PROXY' --impersonate chrome --ai-targeted && cat /tmp/fetch.md" if has_cli else None,
            "action": "use_cli_fallback" if has_cli else "try_ctx_fetch_and_index",
        }


def main():
    parser = argparse.ArgumentParser(
        description="scrapling-fetch v2 — Hermes Agent web fetcher, fully composable",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:
  # Basic fetch (clean markdown, content in context)
  scrapling-fetch.py --url https://example.com

  # Compact: no content in context → agent calls ctx_index
  scrapling-fetch.py --url https://example.com --compact --index-source "docs"
  # Agent reads "action":"call_ctx_index" → calls ctx_index + ctx_search

  # Stealth mode: JS-rendered, Cloudflare bypass
  scrapling-fetch.py --url https://example.com --stealth

  # CSS selectors: extract only what you need
  scrapling-fetch.py --url https://blog.example.com --select "article h1,article p"

  # All links: extract + follow later
  scrapling-fetch.py --url https://example.com --extract-links --max-links 200

  # Raw HTML: no conversion, full raw
  scrapling-fetch.py --url https://example.com --raw

  # Custom impersonation: Chrome/Firefox/Safari
  scrapling-fetch.py --url https://example.com --impersonate chrome_131

  # Disable proxy for localhost/internal URLs
  scrapling-fetch.py --url http://localhost:3000 --no-proxy
        """
    )
    
    parser.add_argument("--url", required=True, help="URL to fetch")
    parser.add_argument("--stealth", action="store_true", help="StealthyFetcher — Playwright browser, JS render, Cloudflare bypass")
    parser.add_argument("--network-idle", action="store_true", default=True, help="Wait for network idle in stealth mode (default: true)")
    parser.add_argument("--no-network-idle", dest="network_idle", action="store_false", help="Don't wait for network idle")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window in stealth mode (debug)")
    parser.add_argument("--raw", action="store_true", help="Return raw HTML instead of markdown")
    parser.add_argument("--text-only", action="store_true", help="Strip ALL tags, return plain text only")
    parser.add_argument("--select", type=str, default="", help="CSS selector for targeted extraction (e.g. 'article p,h1,h2')")
    parser.add_argument("--proxy", type=str, default="", help="Custom proxy URL (default: Webshare rotating). Use 'none' to disable.")
    parser.add_argument("--no-proxy", action="store_true", help="Disable proxy entirely")
    parser.add_argument("--headers", type=str, nargs="*", default=[], help="Extra HTTP headers (Key:Value format, repeatable)")
    parser.add_argument("--extract-links", action="store_true", help="Extract all <a href> links on page")
    parser.add_argument("--max-links", type=int, default=100, help="Max links to extract (default: 100)")
    parser.add_argument("--index-source", type=str, default="", help="Source label for ctx_index. Agent reads 'action' field for next step.")
    parser.add_argument("--compact", action="store_true", help="No content in JSON — agent MUST call ctx_index + ctx_search")
    parser.add_argument("--output-file", type=str, default="", help="Write content to file (e.g. /tmp/fetch.md)")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)")
    parser.add_argument("--impersonate", type=str, default="chrome_131", help="Browser impersonation: chrome_131, firefox_133, safari_18 (default: chrome_131)")
    
    args = parser.parse_args()
    
    # Handle --proxy 'none'
    if args.proxy and args.proxy.lower() == 'none':
        args.no_proxy = True
        args.proxy = ""
    
    result = extract_with_scrapling(args)
    print(json.dumps(result, ensure_ascii=False, indent=2 if not args.compact else None))


if __name__ == "__main__":
    main()
