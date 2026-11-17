# Bot Forge Studio

<p align="center">
<img width="300" height="300" alt="botstudio" src="https://github.com/user-attachments/assets/49e0a137-ef18-45f9-beca-ae5e5fb12b8b" />
</p>

### Build with nodes. Generate real code. Deploy and manage your bots.

**Bot Forge Studio** is a desktop node-based IDE, compiler, and lifecycle manager for Discord bots.

Design bots visually using interconnected nodes, compile them into real, readable **Python projects powered by discord.py**, and optionally generate a companion web dashboard using **Node.js, Express, and WebSockets**.

Unlike visual builders that primarily operate through hosted runtimes, Bot Forge Studio gives developers ownership of their generated source code, deployment environment, and application lifecycle.

**Visual development without sacrificing control over the software you create.**

Concept of the node system is based off of [Conky Studio](https://github.com/bobbycomet/Conky-Studio)

[Windows](#Windows) version information

<p align="center">
<img width="800" height="800" alt="Screenshot_20261116_232255" src="https://github.com/user-attachments/assets/4837a2d2-71f6-4dcc-b420-aeba8b46538e" />
<img width="800" height="800" alt="Screenshot_20261116_232151" src="https://github.com/user-attachments/assets/1a027a81-b390-43a8-8d7a-98bdda3b087b" />
<img width="800" height="800" alt="Screenshot_20261116_232134" src="https://github.com/user-attachments/assets/7ec2b752-64c1-450a-aebd-8b37a1a2bb64" />
<img width="800" height="800" alt="Screenshot_20261116_231934" src="https://github.com/user-attachments/assets/c425bb3e-aa00-4e3e-91a2-30aba08055c6" />
</p>

---

## Why Bot Forge Studio?

Bot Forge Studio brings the visual development process and traditional software development together in one application.

| Capability | Description |
|---|---|
| Visual IDE | Design bot behavior using connected nodes and graphical execution flows |
| Real code generation | Compile projects into readable Python packages using discord.py |
| Web dashboards | Generate optional Node.js/Express dashboards with WebSocket communication |
| Deployment | Deploy locally or to remote machines over SSH |
| Lifecycle management | Start, stop, restart, enable, and monitor bot services |
| Live Sync | Synchronize project changes and hot-reload supported cogs |
| Plugin ecosystem | Extend the editor with Python plugins, `.bsmod` modules, and custom nodes |
| Community modules | Reuse functionality for moderation, events, economy, and games |
| Interactive learning | Built-in tours, guides, and step-by-step bot development lessons |

## Features

### Visual node editor

Build bot functionality by dragging nodes onto a canvas and connecting them together.

The editor separates execution flow from data flow:

- **Execution wires:** Determine the order in which operations run.
- **Data wires:** Pass values and information between nodes.

Supported functionality includes:

| Category | Examples |
|---|---|
| Commands | Slash commands and command options |
| Events | Message, member, and server events |
| Interactions | Buttons, select menus, and modals |
| Actions | Replies, embeds, moderation, and role management |
| Flow control | Conditions, branches, and execution logic |
| Data | Variables, storage, and shared data |
| Economy | Currency, shops, and rewards |
| Scheduling | Timers and scheduled events |
| Web | Pages, routes, and browser-side JavaScript |
| Modules | Reusable functionality and community extensions |

Additional editor features include zooming, panning, grouping, multi-selection, undo/redo, property inspectors, and generated-code inspection.

## Windows

Why is the [Windows](https://ko-fi.com/s/0f440cdbac) version paid?

Bot Forge Studio is primarily developed for Linux. The Linux version is provided free of charge.

The Windows version is offered as a convenience package, including a prebuilt portable ZIP and installer. It provides the same functionality as the Linux version, without requiring users to configure Python, install dependencies, or build the application themselves.

Windows distribution is an additional maintenance and packaging commitment. The purchase supports that work and grants a license to use the Windows distribution. Redistribution is prohibited without explicit permission. Click here for the [Windows](https://ko-fi.com/s/0f440cdbac) version.

### Built on real development experience

Bot Forge Studio is built on functionality originating from eight Discord bots I have personally developed, each with different purposes, features, and implementation requirements. 
Rather than inventing arbitrary functionality to populate a visual editor, I used these existing projects as the foundation for the node system and code-generation architecture.
The functionality represented by the nodes comes from actual implementations, adapted into reusable visual components that the compiler can translate into conventional Python projects using discord.py.
This means the visual editor isn't simply generating theoretical examples or disconnected code templates. Its underlying functionality has roots in real bot-development projects.
Generated code remains yours to inspect, modify, extend, and maintain independently of Bot Forge Studio.
**The objective is to bring real-world Discord bot development into a visual environment without sacrificing the flexibility, transparency, and ownership of conventional software development.**

---

### Real Python code generation

<badge color="success">Readable, editable source</badge>

Bot Forge Studio compiles visual graphs into actual Python projects using discord.py.

Generated projects can include:

| Output | Purpose |
|---|---|
| `bot.py` | Main bot entry point |
| `cogs/` | Bot commands, events, and functionality |
| `bs_runtime.py` | Shared runtime helpers |
| `requirements.txt` | Python dependencies |
| Project manifests | Project configuration |
| Service definitions | Deployment and lifecycle management |


### Optional web dashboard

Generate a companion web application alongside your Discord bot.

| Component | Technology |
|---|---|
| Bot runtime | Python / discord.py |
| Dashboard backend | Node.js / Express |
| Communication | WebSockets |
| Frontend | HTML / CSS / JavaScript |
| Shared functionality | Storage and event bus |

Dashboard capabilities include custom pages, server-side routes, live updates, administrative interfaces, leaderboards, and custom browser interactions.

The dashboard communicates with the bot through shared functionality without exposing the Discord bot token to browser-side code.

### Deployment and lifecycle management

Bot Forge Studio manages the process of turning a project into a running application.

**Deployment targets:**
- Local machines.
- Remote Linux systems over SSH.
- Supported Windows environments.

**Service management:**

| Platform | Support |
|---|---|
| Linux systemd | Yes |
| Linux OpenRC | Yes |
| Linux runit | Yes |
| Windows | Yes |

Additional capabilities include virtual environment creation, dependency installation, project synchronization, service configuration, log viewing, status monitoring, and hot-reloading supported cogs.

### Plugin ecosystem

Extend Bot Forge Studio through:

- Individual `.py` plugins.
- `.bsmod` modules.
- Multi-file module folders.
- Custom node definitions.
- Custom code generators.
- Reusable community modules.

The built-in **Plugin Workshop** provides tools for creating custom nodes directly within the application.

Official and community plugin catalogs are available through the in-app browser and project repository.

Community plugins must use GPLv3 or MIT licensing.

### Community modules

Bot Forge Studio includes reusable functionality for building more advanced Discord communities.

| Module | Functionality |
|---|---|
| Birthdays & Anniversaries | Community date tracking |
| Event Scheduler | Spawns, drops, countdowns, quests, and trades |
| Post Router | Post distribution and routing |
| Role Brackets | Role-based progression |
| Game Profiles | Player and character profiles |
| Task Lists & Reminders | Community task management |
| Game Systems | Scoring, quests, gear, dungeons, bosses, arenas, and cards |

### Interactive learning

Bot Forge Studio isn't just a development environment; it also includes tools to help users learn how to build bots.

- **Interactive tours:** Guided introductions to the application's interface.
- **Guides:** Explanations of features and development workflows.
- **Step-by-step bot lesson:** An interactive lesson that walks users through building a bot, starting with a basic command and progressing toward more advanced functionality.
- **Bot Wizard:** Ready-to-run example projects for moderation, economy, events, faction wars, dashboards, and more.

The generated Python code can be inspected throughout the learning process, helping users understand how visual logic translates into conventional programming.

### Local dashboard testing

The dashboard includes a local testing console that can run against a simulated Discord environment without requiring a bot token.

This makes it possible to develop and test dashboard pages, routes, and browser interactions before connecting an actual Discord application.

---

## Bot Forge Studio vs. other Discord bot builders

Bot Forge Studio is designed around local development, source-code ownership, and deployment control. Browser-based builders offer a different approach, generally prioritizing convenience and managed hosting.

The following comparisons reflect publicly documented functionality, rather than assumptions about undocumented capabilities.

### Web-based platforms

| Capability | Bot Forge Studio | BotGhost | BotWiz | Buildcord |
|---|---|---|---|---|
| Application | Desktop IDE | Web | Web | Web |
| Visual development | Node graphs | Blocks | Blocks | Flows |
| Bot hosting | Your infrastructure | Managed | Managed | Managed |
| Generated Python source | Yes | Not documented | Not documented | Not documented |
| Source-code ownership | Generated project | Platform workflow | Platform workflow | Platform workflow |
| Project export | Python project | Not established | Export supported | JSON blueprint |
| Remote SSH deployment | Yes | No integrated SSH workflow documented | No integrated SSH workflow documented | No integrated SSH workflow documented |
| Service management | Integrated | Platform-managed | Platform-managed | Platform-managed |
| Companion dashboard generation | Express application | Not equivalent | DashWiz | Not equivalent |
| Custom extensions | Python plugins and modules | Platform modules | Modules and Market | Custom JavaScript steps |
| Offline development | Yes | No | No | No |

Official references:
- [BotGhost](https://botghost.com/)
- [BotWiz](https://docs.botwiz.dev/docs/en/getting-started/introduction)
- [Buildcord](https://docs.buildcord.dev/)

*Feature availability and subscription requirements vary by platform. Exporting a platform project or blueprint is not the same as generating a conventional, independently runnable Python source project.*

### Bot Forge Studio vs. Bot Studio for Discord (Steam)

**These are separate projects with different developers.**

Bot Studio for Discord is an existing Windows application distributed through Steam. Bot Forge Studio is an independently developed desktop IDE from Comet UX Labs.

| Capability | Bot Forge Studio | Bot Studio for Discord |
|---|---|---|
| Developer | Comet UX Labs | Bot Studio for Discord / DBS Dev |
| Distribution | Standalone builds | Steam |
| Visual development | Yes | Yes |
| Slash commands | Yes | Yes |
| Buttons and menus | Yes | Yes |
| Server events | Yes | Yes |
| Variables | Yes | Yes |
| Generated discord.py project | Yes | Not advertised |
| Optional Express dashboard | Yes | Not advertised |
| Generated source inspection | Yes | Not advertised |
| SSH deployment | Yes | Not advertised |
| Linux service management | Yes | Not advertised |
| Windows support | Planned standalone EXE | Yes |
| Linux support | AppImage | Not listed |
| Interactive development lessons | Yes | Documentation and community guides |
| Plugin extensibility | Python and `.bsmod` | Community mods |

**Maintenance and development activity**

Bot Studio for Discord's latest publicly visible Steam release announcement is version 2.3, dated June 8, 2025. Its documentation was last updated around May 2025, and community discussions and reviews have raised concerns about its development activity and compatibility.

As of November 2026, its public update history suggests limited recent development activity. This is an observation about publicly visible activity, not confirmation that development has permanently stopped.

Sources:
- [Steam Store listing](https://store.steampowered.com/app/1118380/)
- [Steam update history](https://steamcommunity.com/app/1118380/allnews/)
- [Steam community discussions](https://steamcommunity.com/app/1118380/discussions/)

Bot Forge Studio takes the visual bot-building concept further by combining graphical development with real source generation, optional web application development, deployment, and lifecycle management.

Neither product is affiliated with the other.

---

## Desktop distribution

Bot Forge Studio is being distributed as a standalone desktop application.

| Operating system | Format | Status |
|---|---|---|
| Linux | AppImage | <badge color="success">Build created</badge> |
| Windows | EXE | <badge color="warning">Planned</badge> |

The Linux AppImage has already been created.

The Windows executable is planned to be built through **GitHub Actions**, providing an automated Windows build pipeline.

Both distributions are intended to provide the same core development experience, with platform-specific functionality handled through the appropriate operating-system integrations.

---

## Security and project ownership

Bot Forge Studio is designed to keep development projects and credentials separate.

- Bot tokens are not stored in project files.
- Tokens are written to the deployment target's `.env` file.
- Dashboard browser code does not receive the Discord bot token.
- Privileged operations use polkit on Linux and UAC on Windows.
- Generated source remains accessible and editable.
- Developers control their own deployment infrastructure.

The development environment works offline once installed. Discord connectivity, dependency downloads, external integrations, and remote deployment naturally require network access.

---

## Technology

| Component | Technology |
|---|---|
| Bot runtime | Python |
| Discord library | discord.py |
| Dashboard runtime | Node.js |
| Dashboard backend | Express |
| Dashboard communication | WebSockets |
| Dashboard frontend | HTML / CSS / JavaScript |
| Plugin system | Python / `.bsmod` |
| Linux elevation | polkit |
| Windows elevation | UAC |
| Remote deployment | SSH |

---

## Documentation

Detailed instructions and tutorials are maintained separately from this README.

Visit the project's **GitHub Wiki** for:

- Installation and first-launch instructions.
- Creating your first Discord bot.
- Visual editor tutorials.
- Guided lessons and development workflows.
- Discord application setup, permissions, and intents.
- Deployment and service management.
- Dashboard development.
- Plugin and custom node development.
- Community module documentation.
- Troubleshooting and frequently asked questions.

See the in-app tours, guides, and interactive lessons for guided assistance.

---

## License and credits

|   |   |
|---|---|
| Project | Bot Forge Studio |
| Developer | Comet UX Labs (Bobby Comet) |
| License | GNU GPLv3 |
| Community plugin licenses | GPLv3 or MIT |

Contributions, community modules, and plugins are welcome within the project's licensing requirements.

---

**Build with nodes. Generate real code. Deploy anywhere. Own the code.**

Bot Forge Studio — Comet UX Labs.
