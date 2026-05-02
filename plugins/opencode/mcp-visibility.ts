import type { Plugin } from "@opencode-ai/plugin"
import { createHash } from "node:crypto"
import { existsSync, mkdirSync, readFileSync, writeFileSync, unlinkSync, statSync } from "node:fs"
import { join } from "node:path"

// Security: OFF — OpenCode's native permission system handles this.
// Formatting + caching only.

const CACHE_ENABLED = process.env.MCP_VISIBILITY_CACHE !== "0"
const FMT_MODE = process.env.MCP_VISIBILITY_FMT || "smart"
const CACHE_DIR = join(process.env.TMPDIR || "/tmp", "opencode-mcp-vis-cache")
const TRUNCATE_LINES = 100
const SMALL_THRESHOLD = 200

// ═══════════════════════════════════════════════════════════
// Smart Formatting (matching output_fmt.py)
// ═══════════════════════════════════════════════════════════

function makeHeader(fmt: string, meta: Record<string, string | number>): string {
  const parts = [`fmt=${fmt}`]
  if (meta.rows) { parts.push(`rows=${meta.rows}`); if (meta.cols) parts.push(`cols=${meta.cols}`) }
  if (meta.saved != null || meta.saved_pct != null) {
    const val = Math.round(Number(meta.saved ?? meta.saved_pct ?? 0))
    parts.push(`saved=${val}%`)
  }
  return `[vis ${parts.join(" ")}]`
}

function toMarkdownTable(rows: Record<string, unknown>[], keys: string[]): string {
  if (rows.length === 0) return "_(empty)_"
  const header = "| " + keys.join(" | ") + " |"
  const sep = "| " + keys.map(() => "---").join(" | ") + " |"
  const body = rows.map(row =>
    "| " + keys.map(k => String(row[k] ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ")).join(" | ") + " |"
  )
  return [header, sep, ...body].join("\n")
}

function toYAMLish(obj: Record<string, unknown>): string {
  const lines: string[] = []
  for (const [k, v] of Object.entries(obj)) {
    if (Array.isArray(v) && v.length > 0 && v.every(x => typeof x === "object")) {
      lines.push(`${k}:`)
      for (const item of v) lines.push(`  - ${JSON.stringify(item)}`)
    } else if (typeof v === "string" && v.length > 60 && !v.includes("\n")) {
      lines.push(`${k}: "${v}"`)
    } else if (typeof v === "string") {
      lines.push(`${k}: ${v}`)
    } else if (typeof v === "number" || typeof v === "boolean" || v === null) {
      lines.push(`${k}: ${v}`)
    } else {
      lines.push(`${k}: ${JSON.stringify(v)}`)
    }
  }
  return lines.join("\n")
}

function stripHTML(html: string): string {
  let text = html.replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ")
  text = text.replace(/<[^>]+>/g, " ")
  text = text.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  text = text.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, " ")
  text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n")
  return text.trim()
}

function formatResult(raw: string, _toolName: string): string {
  if (FMT_MODE === "passthrough") return raw

  const stripped = raw.trim()
  if (!stripped) return raw
  const originalBytes = Buffer.byteLength(raw, "utf-8")

  let parsed: unknown
  try { parsed = JSON.parse(stripped) } catch { parsed = null }

  if (parsed !== null) {
    if ((Array.isArray(parsed) || typeof parsed === "object") && Object.keys(parsed as object).length === 0)
      return raw

    if (Array.isArray(parsed) && parsed.length > 0 && parsed.every(x => typeof x === "object" && x !== null)) {
      const keys = Object.keys(parsed[0] as object)
      if (keys.length <= 8) {
        const table = toMarkdownTable(parsed as Record<string, unknown>[], keys)
        const fmtBytes = Buffer.byteLength(table, "utf-8")
        const savings = Math.round((1 - fmtBytes / Math.max(originalBytes, 1)) * 100)
        const header = makeHeader("md-table", { rows: parsed.length, cols: keys.length, saved_pct: savings })
        return `${header}\n${table}`
      }
    }

    if (typeof parsed === "object" && !Array.isArray(parsed) && parsed !== null) {
      if (originalBytes < SMALL_THRESHOLD) return raw
      const yaml = toYAMLish(parsed as Record<string, unknown>)
      const fmtBytes = Buffer.byteLength(yaml, "utf-8")
      const savings = Math.round((1 - fmtBytes / Math.max(originalBytes, 1)) * 100)
      const header = makeHeader("yaml", { saved_pct: savings })
      return `${header}\n${yaml}`
    }

    return raw
  }

  if (stripped.startsWith("<") && (stripped.includes("</") || stripped.includes("/>"))) {
    const text = stripHTML(stripped)
    if (text) {
      const fmtBytes = Buffer.byteLength(text, "utf-8")
      const savings = Math.round((1 - fmtBytes / Math.max(originalBytes, 1)) * 100)
      const header = makeHeader("markdown", { saved_pct: savings })
      return `${header}\n${text}`
    }
  }

  const lines = stripped.split("\n")
  if (lines.length > TRUNCATE_LINES) {
    const truncated = lines.slice(0, TRUNCATE_LINES).join("\n")
    const leftover = lines.length - TRUNCATE_LINES
    const footer = `\n[... omitted=${leftover} more lines. Use search to query specific sections.]`
    const result = truncated + footer
    const fmtBytes = Buffer.byteLength(result, "utf-8")
    const savings = Math.round((1 - fmtBytes / Math.max(originalBytes, 1)) * 100)
    const header = makeHeader("truncated", { rows: TRUNCATE_LINES, saved_pct: savings })
    return `${header}\n${result}`
  }

  return raw
}

// ═══════════════════════════════════════════════════════════
// TOON fallback
// ═══════════════════════════════════════════════════════════

function jsonToToon(data: unknown): string {
  if (Array.isArray(data)) {
    if (data.length === 0) return "[]"
    if (data.every(x => typeof x === "object" && x !== null)) {
      const keys = Object.keys(data[0] as object)
      return `items[${data.length}]{${keys.join(",")}}\n` +
        data.map(item => `  ${keys.map(k => String((item as any)[k] ?? "")).join(",")}`).join("\n")
    }
    return JSON.stringify(data)
  }
  if (typeof data === "object" && data !== null) {
    const parts: string[] = []
    for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
      if (Array.isArray(v) && v.length > 0 && v.every(x => typeof x === "object")) {
        const keys = Object.keys(v[0] as object)
        parts.push(`${k}[${v.length}]{${keys.join(",")}}`)
        for (const item of v) parts.push(`  ${keys.map(kk => String((item as any)[kk] ?? "")).join(",")}`)
      } else if (typeof v === "number" || typeof v === "boolean" || v === null) {
        parts.push(`${k}=${v}`)
      } else if (typeof v === "string") {
        parts.push(/[,=\n ]/.test(v) ? `${k}=${JSON.stringify(v)}` : `${k}=${v}`)
      } else {
        parts.push(`${k}=${JSON.stringify(v)}`)
      }
    }
    return parts.join("\n")
  }
  return JSON.stringify(data)
}

function toonConvert(dataStr: string): string {
  const stripped = dataStr.trim()
  if (!(stripped.startsWith("{") || stripped.startsWith("["))) return dataStr
  try {
    const toon = jsonToToon(JSON.parse(stripped))
    return toon.length < dataStr.length ? toon : dataStr
  } catch { return dataStr }
}

function optimize(raw: string, toolName: string): string {
  if (FMT_MODE === "toon") return toonConvert(raw)
  if (FMT_MODE === "passthrough") return raw
  return formatResult(raw, toolName)
}

// ═══════════════════════════════════════════════════════════
// Cache
// ═══════════════════════════════════════════════════════════

function cacheKey(tool: string, args: Record<string, unknown>): string {
  return createHash("sha256").update(`${tool}:${JSON.stringify(args, Object.keys(args).sort())}`).digest("hex").slice(0, 16)
}

function cacheGet(tool: string, args: Record<string, unknown>): string | null {
  if (!CACHE_ENABLED) return null
  try {
    const p = join(CACHE_DIR, `${cacheKey(tool, args)}.result`)
    if (!existsSync(p)) return null
    if ((Date.now() - statSync(p).mtimeMs) / 1000 > 60) { unlinkSync(p); return null }
    return readFileSync(p, "utf-8")
  } catch { return null }
}

function cacheSet(tool: string, args: Record<string, unknown>, result: string): void {
  if (!CACHE_ENABLED) return
  try {
    if (!existsSync(CACHE_DIR)) mkdirSync(CACHE_DIR, { recursive: true })
    writeFileSync(join(CACHE_DIR, `${cacheKey(tool, args)}.result`), result)
  } catch { /* silent */ }
}

// ═══════════════════════════════════════════════════════════
// Plugin
// ═══════════════════════════════════════════════════════════

function isShellTool(tool: string): boolean {
  return tool.includes("ctx_execute") || tool.includes("ctx_batch")
}

function isOptimizable(tool: string): boolean {
  return tool.includes("ctx_") || tool.includes("searxng") || tool.includes("web_search") || tool.includes("web_read")
}

export const McpVisibilityPlugin: Plugin = async ({}) => {
  return {
    "tool.execute.before": async (input, output: any) => {
      const tool = String(input?.tool ?? "")
      if (!isShellTool(tool)) return

      const args = output?.args as Record<string, unknown> | undefined
      if (!args) return
      const inner = (args.arguments ?? args) as Record<string, unknown>
      const code = String(inner.code ?? "")
      const lang = String(inner.language ?? "")

      if (lang !== "shell" || !code) return

      // Cache check BEFORE execution
      const cached = cacheGet(tool, args)
      if (cached) {
        (output as any)._cacheResult = cached
        inner.code = "true # cache-hit"
      }
    },

    "tool.execute.after": async (input, output: any) => {
      const tool = String(input?.tool ?? "")
      if (!isOptimizable(tool)) return

      // Cache hit → use cached result
      if ((output as any)._cacheResult) {
        output.output = (output as any)._cacheResult
        delete (output as any)._cacheResult
        return
      }

      const raw = typeof output?.output === "string" ? output.output : ""
      if (!raw) return

      const args = (input?.args ?? {}) as Record<string, unknown>
      const formatted = optimize(raw, tool)
      output.output = formatted

      cacheSet(tool, args, formatted)
    },
  }
}
