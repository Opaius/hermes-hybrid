#!/usr/bin/env node
/**
 * hermes-hybrid — CLI
 * 
 * Context-mode + mcp-visibility + RTK for Hermes Agent & OpenCode.
 * One setup wires all three.
 * 
 * Usage:
 *   hermes-hybrid setup      Auto-detect + install everything
 *   hermes-hybrid serve      Start context-mode MCP server
 *   hermes-hybrid doctor     Health check all integrations
 *   hermes-hybrid status     Show what's running, enabled, versions
 *   hermes-hybrid remove     Clean uninstall
 *   hermes-hybrid env        Print current env vars + effects
 */

const fs = require("fs");
const path = require("path");
const os = require("os");
const { execSync, spawn } = require("child_process");

const HOME = os.homedir();
const PKG_DIR = path.resolve(__dirname, "..");
const HERMES_HOME = process.env.HERMES_HOME || path.join(HOME, ".hermes");
const OPENCODE_CONFIG = path.join(HOME, ".config", "opencode");

const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED = "\x1b[31m";
const CYAN = "\x1b[36m";
const BOLD = "\x1b[1m";
const NC = "\x1b[0m";

const BANNER = `
${CYAN}╔══════════════════════════════════════════╗
║   hermes-hybrid v1.0.0                    ║
║   Token efficiency for Hermes + OpenCode  ║
╚══════════════════════════════════════════╝${NC}
`;

function ok(msg)  { console.log(`  ${GREEN}✓${NC} ${msg}`); }
function warn(msg) { console.log(`  ${YELLOW}⚠${NC} ${msg}`); }
function err(msg)  { console.log(`  ${RED}✗${NC} ${msg}`); }
function info(msg) { console.log(`  ${CYAN}→${NC} ${msg}`); }
function section(msg) { console.log(`\n${BOLD}${msg}${NC}`); }

function cmd(cmd, opts = {}) {
  try {
    return execSync(cmd, { stdio: opts.silent ? "pipe" : "inherit", ...opts }).toString().trim();
  } catch (e) {
    return null;
  }
}

function hasCmd(name) {
  return cmd(`which ${name} 2>/dev/null || command -v ${name} 2>/dev/null`, { silent: true }) !== null;
}

// ─── SETUP ────────────────────────────────────────────

function setup() {
  console.log(BANNER);
  
  section("Detecting environment...");
  
  const hermesInstalled = fs.existsSync(path.join(HERMES_HOME, "config.yaml"));
  const opencodeInstalled = fs.existsSync(path.join(OPENCODE_CONFIG, "config.json"));
  const hasBun = hasCmd("bun");
  const hasNode = hasCmd("node");
  
  hermesInstalled ? ok("Hermes Agent found") : warn("Hermes Agent not detected");
  opencodeInstalled ? ok("OpenCode found") : warn("OpenCode not detected");
  hasBun ? ok("bun found") : hasNode ? warn("bun not found — node fallback") : err("No JS runtime found");

  // ─── Install mcp-visibility plugins ───
  section("Installing mcp-visibility plugins...");

  // Hermes plugin
  if (hermesInstalled) {
    const hermesPluginDir = path.join(HERMES_HOME, "plugins", "mcp-visibility");
    fs.mkdirSync(hermesPluginDir, { recursive: true });
    const hermesFiles = ["__init__.py", "mcp_visibility.py", "security.py", "output_fmt.py", "plugin.yaml"];
    for (const f of hermesFiles) {
      fs.copyFileSync(path.join(PKG_DIR, "plugins", "hermes", f), path.join(hermesPluginDir, f));
    }
    ok("Hermes mcp-visibility plugin → ~/.hermes/plugins/mcp-visibility/");
    
    // Add to config.yaml if not already there
    const configPath = path.join(HERMES_HOME, "config.yaml");
    if (fs.existsSync(configPath)) {
      let config = fs.readFileSync(configPath, "utf8");
      if (!config.includes("mcp-visibility")) {
        // Simple check — user should verify
        info("Add 'mcp-visibility' to plugins list in ~/.hermes/config.yaml");
      } else {
        ok("mcp-visibility already in config.yaml");
      }
    }
  }

  // OpenCode plugin
  if (opencodeInstalled) {
    const ocPluginDir = path.join(OPENCODE_CONFIG, "plugins");
    fs.mkdirSync(ocPluginDir, { recursive: true });
    fs.copyFileSync(
      path.join(PKG_DIR, "plugins", "opencode", "mcp-visibility.ts"),
      path.join(ocPluginDir, "mcp-visibility.ts")
    );
    ok("OpenCode mcp-visibility plugin → ~/.config/opencode/plugins/");
  }

  // ─── Configure context-mode MCP server ───
  section("Configuring context-mode MCP server...");
  
  const ctxModeInstalled = cmd("bunx context-mode --version 2>/dev/null", { silent: true }) !== null 
    || cmd("npx context-mode --version 2>/dev/null", { silent: true }) !== null;
  
  if (ctxModeInstalled) {
    ok("context-mode MCP server available");
  } else {
    warn("context-mode not installed — run: bunx context-mode --help");
  }

  // Add context-mode to Hermes MCP servers
  if (hermesInstalled) {
    const configPath = path.join(HERMES_HOME, "config.yaml");
    if (fs.existsSync(configPath)) {
      let config = fs.readFileSync(configPath, "utf8");
      if (!config.includes("context-mode")) {
        info("Add context-mode to mcp_servers in ~/.hermes/config.yaml:\n" +
             "    - name: context-mode\n" +
             "      command: bunx\n" +
             "      args: [\"context-mode\"]\n" +
             "      env:\n" +
             "        SEARXNG_URL: http://localhost:3211");
      } else {
        ok("context-mode already in config.yaml");
      }
    }
  }

  // ─── Install rtk ───
  section("Checking RTK...");
  let rtkPath = null;
  if (hasCmd("rtk")) {
    rtkPath = cmd("which rtk 2>/dev/null", { silent: true }) || cmd("command -v rtk 2>/dev/null", { silent: true });
    ok(`RTK installed: ${cmd("rtk --version 2>/dev/null", { silent: true }) || "✓"} (${rtkPath})`);
  } else {
    warn("RTK not installed — attempting auto-install...");
    // Try npm first (faster), fall back to curl
    const npmOk = cmd("npm i -g rtk 2>/dev/null", { silent: true }) !== null;
    if (!npmOk) {
      info("npm install failed, trying bun...");
      const bunOk = cmd("bun i -g rtk 2>/dev/null", { silent: true }) !== null;
      if (!bunOk) {
        info("Bun install failed, trying curl...");
        cmd("curl -fsSL https://rtk.ai/install.sh | bash 2>/dev/null", { silent: true });
      }
    }
    if (hasCmd("rtk")) {
      rtkPath = cmd("which rtk 2>/dev/null", { silent: true });
      ok(`RTK installed successfully (${rtkPath})`);
    } else {
      err("RTK install failed — manual install: curl -fsSL https://rtk.ai/install.sh | bash");
    }
  }

  // Ensure rtk is in context-mode startup PATH
  if (rtkPath && rtkPath !== "/usr/bin/rtk" && !fs.existsSync("/usr/bin/rtk")) {
    try {
      fs.symlinkSync(rtkPath, "/usr/bin/rtk");
      ok("RTK symlinked to /usr/bin/rtk (context-mode compat)");
    } catch {
      info("Could not symlink rtk to /usr/bin — may need sudo");
    }
  } else if (fs.existsSync("/usr/bin/rtk")) {
    ok("RTK already at /usr/bin/rtk ✓");
  }

  // ─── Done ───
  section("Setup complete!");
  console.log(`\n  ${GREEN}Next steps:${NC}`);
  console.log(`  • Restart Hermes:  hermes gateway restart`);
  console.log(`  • Verify:          hermes-hybrid doctor`);
  console.log(`  • Start server:    hermes-hybrid serve`);
  console.log(`  • See all options: hermes-hybrid env\n`);
}

// ─── SERVE ────────────────────────────────────────────

function serve() {
  console.log(BANNER);
  section("Starting context-mode MCP server...");
  
  const serverEnv = {
    ...process.env,
    HH_SANDBOX: process.env.HH_SANDBOX || "bun",
    HH_SERVER: "1",
  };

  if (process.env.HH_SERVER === "0") {
    err("HH_SERVER=0 — server disabled");
    process.exit(1);
  }

  const runtime = hasCmd("bun") ? "bunx" : "npx";
  info(`Using ${runtime} context-mode`);
  console.log(`  Press Ctrl+C to stop\n`);

  const child = spawn(runtime, ["context-mode"], {
    stdio: "inherit",
    env: serverEnv,
  });

  child.on("exit", (code) => {
    console.log(`\n  Server exited with code ${code}`);
  });
}

// ─── DOCTOR ───────────────────────────────────────────

function doctor() {
  console.log(BANNER);
  console.log("  Checking all integrations...\n");

  const checks = [
    ["Hermes Agent", fs.existsSync(path.join(HERMES_HOME, "config.yaml"))],
    ["Hermes mcp-visibility plugin", fs.existsSync(path.join(HERMES_HOME, "plugins", "mcp-visibility", "__init__.py"))],
    ["OpenCode", fs.existsSync(path.join(OPENCODE_CONFIG, "config.json"))],
    ["OpenCode mcp-visibility plugin", fs.existsSync(path.join(OPENCODE_CONFIG, "plugins", "mcp-visibility.ts"))],
    ["context-mode MCP server", cmd("bunx context-mode --version 2>/dev/null", { silent: true }) !== null],
    ["RTK", hasCmd("rtk")],
    ["bun runtime", hasCmd("bun")],
    ["node runtime", hasCmd("node")],
  ];

  let allGood = true;
  for (const [name, ok_] of checks) {
    ok_ ? ok(name) : (err(name), allGood = false);
  }

  console.log(`\n  ${allGood ? GREEN + "All checks passed!" + NC : YELLOW + "Some checks failed — run 'hermes-hybrid setup'" + NC}`);
}

// ─── STATUS ───────────────────────────────────────────

function status() {
  console.log(BANNER);
  console.log("  Environment:\n");
  
  const vars = {
    "HH_SERVER": process.env.HH_SERVER || "1",
    "HH_VISIBILITY": process.env.HH_VISIBILITY || "1",
    "HH_FMT": process.env.HH_FMT || "smart",
    "HH_SECURITY": process.env.HH_SECURITY || "1",
    "HH_CACHE": process.env.HH_CACHE || "1",
    "HH_COMPACT": process.env.HH_COMPACT || "1",
    "HH_SANDBOX": process.env.HH_SANDBOX || "bun",
    "HERMES_HOME": HERMES_HOME,
  };

  for (const [k, v] of Object.entries(vars)) {
    const label = k.padEnd(18);
    console.log(`  ${CYAN}${label}${NC} = ${v}`);
  }
}

// ─── ENV ──────────────────────────────────────────────

function envHelp() {
  console.log(BANNER);
  console.log(`  ${BOLD}Environment variables:${NC}\n`);
  
  const vars = [
    ["HH_SERVER=0",        "Disable context-mode MCP server entirely"],
    ["HH_VISIBILITY=0",    "Disable mcp-visibility (formatting + security + cache)"],
    ["HH_FMT=passthrough", "Formatting mode: smart | toon | passthrough"],
    ["HH_SECURITY=0",      "Disable command security checks"],
    ["HH_CACHE=0",         "Disable result caching"],
    ["HH_COMPACT=0",       "Disable schema description compaction"],
    ["HH_SANDBOX=bun",     "Sandbox runtime: bun | deno | node"],
    ["HH_TRUNCATE=100",    "Lines before truncation (default 100)"],
  ];

  for (const [v, desc] of vars) {
    console.log(`  ${GREEN}${v.padEnd(25)}${NC} ${desc}`);
  }

  console.log(`\n  ${YELLOW}Set in ~/.hermes/.env or export before running.${NC}\n`);
}

// ─── REMOVE ───────────────────────────────────────────

function remove() {
  console.log(BANNER);
  console.log(`  ${RED}This will remove all hermes-hybrid components.${NC}\n`);

  const targets = [
    [path.join(HERMES_HOME, "plugins", "mcp-visibility"), "Hermes mcp-visibility plugin"],
    [path.join(OPENCODE_CONFIG, "plugins", "mcp-visibility.ts"), "OpenCode mcp-visibility plugin"],
  ];

  for (const [p, name] of targets) {
    if (fs.existsSync(p)) {
      fs.rmSync(p, { recursive: true, force: true });
      ok(`Removed: ${name}`);
    } else {
      warn(`Not found: ${name}`);
    }
  }

  console.log(`\n  ${YELLOW}Note: context-mode and RTK are system-wide — uninstall separately if needed.${NC}\n`);
}

// ─── MAIN ─────────────────────────────────────────────

const cmd_ = process.argv[2] || "help";

switch (cmd_) {
  case "setup":   setup(); break;
  case "serve":   serve(); break;
  case "doctor":  doctor(); break;
  case "status":  status(); break;
  case "remove":  remove(); break;
  case "env":     envHelp(); break;
  case "help":
  case "--help":
  case "-h":
    console.log(BANNER);
    console.log(`  ${BOLD}Usage:${NC} hermes-hybrid <command>\n`);
    console.log(`  ${GREEN}setup${NC}      Auto-detect + install everything`);
    console.log(`  ${GREEN}serve${NC}      Start context-mode MCP server`);
    console.log(`  ${GREEN}doctor${NC}     Health check all integrations`);
    console.log(`  ${GREEN}status${NC}     Show current configuration`);
    console.log(`  ${GREEN}remove${NC}     Clean uninstall`);
    console.log(`  ${GREEN}env${NC}        Show environment variables\n`);
    console.log(`  ${YELLOW}Alias:${NC} hh <command>\n`);
    break;
  default:
    console.log(`Unknown command: ${cmd_}`);
    console.log(`Try: hermes-hybrid help`);
    process.exit(1);
}
