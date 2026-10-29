# Quality gate setup: .NET

Templates are in `${CLAUDE_SKILL_DIR}/templates/dotnet/`. Versions were verified in October 2026; pin them in the repo.

## Prerequisites on the machine (and in CI)

- **.NET SDK** 8 or later.
- **Python 3.9+** for `gate.py`.
- **lizard and diff-cover:**

  ```bash
  pipx install lizard==1.24.3 && pipx install diff-cover    # or: uv tool install …
  ```

  diff-cover 11 needs Python 3.11 or later. pipx and uv bring their own Python.
- **Node.js** (for `npx jscpd@5.4.2`, which the gate pins itself).

## Files to add or merge

| Template | Goes to | Notes |
|---|---|---|
| `atdd.json` | `.claude/atdd.json` | Replace `__App__`, the paths, the solution name and the commands with the repo's real ones |
| `reqnroll.json` | the acceptance test project's root | `missingOrPendingStepsOutcome: Error`, so undefined steps fail |
| `stryker-config.json` | repo root | Mutation level and excludes. The gate passes `--solution`, `--since:<base>` and thresholds on the command line |
| `SonarLint.xml` | repo root | S3776 threshold. Keep it equal to `gate.thresholds.cognitive` |
| `CodeMetricsConfig.txt` | repo root | CA1502/CA1505/CA1506 thresholds. Keep CA1502 equal to `gate.thresholds.cyclomatic` |
| `editorconfig-atdd.ini` | append to `.editorconfig` | Turns on S3776, CA1502/1505/1506/1509 and IDE0051/52/60 as **warnings**. The gate fails on warnings in changed code only, so existing code still builds |
| `Directory.Build.atdd.props` | merge into `Directory.Build.props` | Adds `SonarAnalyzer.CSharp`, `EnforceCodeStyleInBuild`, and both AdditionalFiles. With Central Package Management, put the version in `Directory.Packages.props` |
| `dotnet-tools.json` | `.config/dotnet-tools.json` (merge) | `dotnet-stryker` 5.0.0. Run `dotnet tool restore` |
| `${CLAUDE_SKILL_DIR}/scripts/gate.py` | `scripts/atdd/gate.py` | The gate itself. Copy it unchanged |
| `../common/qa-up.sh`, `../common/qa-down.sh` | `scripts/atdd/` | Adapt `qa-up.sh` to start the API (and `docker compose` dependencies) |

**Test projects** need `coverlet.collector` (VSTest) for coverage:

```xml
<PackageReference Include="coverlet.collector" Version="6.*" PrivateAssets="all" />
```

**On .NET 10 with Microsoft.Testing.Platform** (`global.json` → `"test": {"runner": "Microsoft.Testing.Platform"}`), `coverlet.collector` doesn't work. Use `Microsoft.Testing.Extensions.CodeCoverage` and override the coverage run:

```json
"gate": {
  "commands": {
    "testsWithCoverage": "dotnet test --solution __App__.sln --coverage --coverage-output-format cobertura --results-directory \"$ATDD_OUT/test-results\""
  },
  "coverageReports": [".atdd/**/test-results/**/*.cobertura.xml"]
}
```

## Architecture rules

The gate detects ArchUnitNET or NetArchTest tests and relies on the test run to enforce them. Until the repo has some, the architecture check reports `skipped`. Write the rules from `docs/architecture.md` and the ADRs into a test project (e.g. `tests/__App__.ArchitectureTests`):

```csharp
using ArchUnitNET.Domain; using ArchUnitNET.Loader; using ArchUnitNET.xUnit; using Xunit;
using static ArchUnitNET.Fluent.ArchRuleDefinition;

public class LayeringTests
{
    static readonly Architecture Arch = new ArchLoader()
        .LoadAssemblies(typeof(App.Domain.AssemblyMarker).Assembly, typeof(App.Infrastructure.AssemblyMarker).Assembly).Build();

    [Fact]
    public void Domain_does_not_depend_on_infrastructure() =>
        Types().That().ResideInNamespaceOrBelow("App.Domain").Should()
            .NotDependOnAny(Types().That().ResideInNamespaceOrBelow("App.Infrastructure"))
            .Because("the domain is persistence-ignorant (ADR-004)").Check(Arch);
}
```

`AssemblyMarker` stands for any type in that assembly. `ResideInNamespaceOrBelow` needs `TngTech.ArchUnitNET.xUnit` 0.13.5 or later.

## How the gate drives the tools

- **Build:** `dotnet build <solution> --no-incremental --tl:off -clp:NoSummary;ForceNoAlign;DisableConsoleColor`.
  - `--no-incremental` makes analyzers re-report on every run.
  - `--tl:off` turns off the terminal logger, whose output isn't meant for parsing.
  - The gate parses `file(line,col): warning CODE: message` lines. It keeps those in changed files that sit on an added line or inside a changed function (function ranges come from lizard).
- **Format:** `dotnet format whitespace . --folder --verify-no-changes --include <changed .cs files>`. Exit 2 means files need formatting. Style rules come from the build (`EnforceCodeStyleInBuild`).
- **Mutation:** `dotnet stryker --solution <sln> --since:<base> -r json … -O <out>/stryker`.
  - Stryker exits 2 below `--break-at`.
  - The gate computes its own score over mutants on **changed lines** from `<out>/stryker/reports/mutation-report.json`.
  - `--since` needs the base commit locally. In CI, fetch enough history (`fetch-depth: 0`).
- **Vulnerabilities:** `dotnet package list --vulnerable --include-transitive --format json` on SDK 10+, falling back to `dotnet list package` on older SDKs. Both exit 0 even when they find vulnerabilities, so the gate counts the findings in the JSON itself.

**Not verified end-to-end:** the .NET path was written against current docs and tool source. It wasn't run against a real solution while this plugin was built. The first time you run `/atdd:init` and the gate in a .NET repo, read `.atdd/quality/latest/logs/*.log` once to confirm every tool ran as expected.
