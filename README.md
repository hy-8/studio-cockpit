# Local Project Cockpit

A local-first dashboard for searching and organizing notes, plans, project logs, and tool entry points across multiple workspaces. It indexes text files on your machine and serves a dashboard on `127.0.0.1`; your source documents stay where they are.

Python 3.10+, standard library only.

## Start

1. Install Python 3.10 or newer.
2. Copy `驾驶舱配置.example.json` to `驾驶舱配置.json` and edit `roots` to point at folders on your machine.
3. (Optional) Copy `tools.example.json` to `tools.json`, then replace each example tool with your own entries and local paths.
4. Run `python cockpit.py doctor`, then `python cockpit.py scan`.
5. Run `python cockpit.py serve` and open <http://127.0.0.1:8766>.

The dashboard provides project activity summaries, full-text search, knowledge-document grouping, a local secret scan, context-pack generation, and a tool launcher. It is designed for personal local use; do not expose its server to a public network.

## CLI

```sh
python cockpit.py search "topic words"
python cockpit.py status
python cockpit.py recent 14
python cockpit.py knowledge
python cockpit.py security
python cockpit.py pack project-name
python cockpit.py scan
python cockpit.py doctor
```

## Keep your data private

The index, generated context packs, real configuration, and personal reports are intentionally excluded from Git. The example configs contain placeholders only. Set your workspace roots locally; do not commit source documents, generated outputs, credentials, account data, or machine-specific paths.

## Make a cockpit for your own projects

Use the prompt in [`AGENT_PROMPT.md`](AGENT_PROMPT.md) with a coding agent that can read your project folders. It asks the agent to inventory the workspaces, identify useful recurring views and actions, then implement a local dashboard with configurable roots and private data excluded from version control.

## Configuration

`驾驶舱配置.example.json` shows the supported basic options. `tools.example.json` documents the tool-card format. Copy either to the corresponding untracked local filename before editing. Keep local configuration and generated data out of commits.

## License

MIT. See [`LICENSE`](LICENSE).
