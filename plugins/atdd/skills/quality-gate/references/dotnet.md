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
| `dotnet-quality/` | `scripts/atdd/dotnet-quality/` | The gate's C# helper for NPath and the namespace graph. It compiles against the Roslyn that ships with the SDK, so it needs no NuGet packages, ignores the repo's `Directory.Build.props` and Central Package Management, and builds under `.atdd/cache/`. Don't add it to the solution |
| `${CLAUDE_SKILL_DIR}/scripts/gate.py` | `scripts/atdd/gate.py` | The gate itself. Copy it unchanged |
| `../common/qa-up.sh`, `../common/qa-down.sh` | `scripts/atdd/` | Adapt `qa-up.sh` to start the API (and `docker compose` dependencies) |

**Test projects** need `coverlet.collector` (VSTest) for coverage:

```xml
<PackageReference Include="coverlet.collector" Version="6.*" PrivateAssets="all" />
```

The gate asks the collector for two formats, `--collect:"XPlat Code Coverage;Format=json,cobertura"`. diff-cover reads the Cobertura reports for lines. The JSON reports name every branch, so a branch counts as taken when any test project took it: an acceptance test can take one side of an `if` and a unit test the other.

**On .NET 10 with Microsoft.Testing.Platform** (`global.json` → `"test": {"runner": "Microsoft.Testing.Platform"}`), `coverlet.collector` doesn't work. Use `Microsoft.Testing.Extensions.CodeCoverage` and override the coverage run:

```json
"gate": {
  "commands": {
    "testsWithCoverage": "dotnet test --solution __App__.sln --coverage --coverage-output-format cobertura --results-directory \"$ATDD_OUT/test-results\""
  },
  "coverageReports": [".atdd/**/test-results/**/*.cobertura.xml"]
}
```

Those reports are Cobertura only, so for each changed line the gate takes the branch counts of the test project that covered most of them. When one line's branches are split across test projects, either cover them from one project, or merge the reports first with `dotnet-coverage merge --output-format cobertura` (it combines branches exactly) and point `coverageReports` at the merged file.

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
- **NPath:** `dotnet run --project scripts/atdd/dotnet-quality -c Release -- npath <file list> <out.json>`. It measures every method, constructor, accessor, operator, local function and lambda in the changed files, from the syntax tree alone, so it runs before or without a build. Switch expressions count their arms, and pattern combinators (`and`, `or`) count like `&&` and `||`.
- **Dependency cycles:**
  - Namespaces: the same helper with `namespaces` compiles all production code under `gate.sourceDirs` and records which namespace's code uses a type declared in another (any use: a parameter, a `new`, a static call, an attribute). `using` directives alone don't count. Compile errors don't stop it; a type it can't resolve just adds no dependency.
  - Projects: the `ProjectReference`s of every `*.csproj` in the repo. MSBuild refuses to build such a cycle, so the build check usually reports it first.
- **Tests:** `dotnet test <solution> --logger trx --results-directory <out>/test-results`, plus `--collect:"XPlat Code Coverage;Format=json,cobertura"` in full mode, which writes one `coverage.json` and one `coverage.cobertura.xml` per test project.
- **Flaky tests:** when tests fail, the gate reads the failures from the `.trx` files and classifies each one by project (see "Flaky tests (.NET)" in the skill). For that to work:
  - Keep tests on VSTest (the default runner). The TRX logger names each failing test's class, method and assembly; the gate maps the assembly back to the test project above its `bin/` folder. On Microsoft.Testing.Platform, with a `testsWithCoverage` override, failures aren't classified.
  - Reference production code through `ProjectReference`. A test project that compiles production files in with `<Compile Include="../…" />` or reaches them through a package hides the dependency, so a failure there can read as flaky when it isn't.
  - Keep `.feature` files and step definitions inside the Reqnroll test project, so a change to them counts as a change to that project.
  - In full mode, flaky tests are left out of the coverage run with `--filter "FullyQualifiedName!=<class>.<method>&…"` (every case of a theory goes) and out of Stryker.NET through `-f <out>/stryker-config.json`: a copy of the repo's `stryker-config.json` with `test-case-filter` set (joined with `&` to any filter already there). A `gate.commands.mutation` override must apply `$ATDD_FLAKY_TEST_FILTER` itself.
- **Mutation:** `dotnet stryker --solution <sln> --since:<base> -r json … -O <out>/stryker`.
  - Stryker exits 2 below `--break-at`.
  - The gate computes its own score over mutants on **changed lines** from `<out>/stryker/reports/mutation-report.json`.
  - `--since` needs the base commit locally. In CI, fetch enough history (`fetch-depth: 0`).
- **Vulnerabilities:** `dotnet package list --vulnerable --include-transitive --format json` on SDK 10+, falling back to `dotnet list package` on older SDKs. Both exit 0 even when they find vulnerabilities, so the gate counts the findings in the JSON itself.

## Cover mode (/atdd:cover)

- **Acceptance coverage:** `<commands.acceptanceTests> --collect:"XPlat Code Coverage;Format=cobertura" --results-directory <out>/acceptance-coverage/test-results`, so `commands.acceptanceTests` must be a plain `dotnet test <acceptance test project>`. On Microsoft.Testing.Platform, set `gate.commands.acceptanceCoverage` and `gate.acceptanceCoverageReports` the way `testsWithCoverage` is set above.
- **Acceptance mutation:** `dotnet stryker --solution <sln> -m "**/<file name>" … --test-case-filter "<filter>" -r json … -O <out>/acceptance-stryker`.
  - The filter, `gate.acceptanceTestFilter`, defaults to `FullyQualifiedName~Acceptance`: the same convention as the template's `commands.unitTests`.
  - `-m` matches file names, so a same-named file in another project is mutated too. The gate only counts mutants in the targets, so that costs time but doesn't skew the result.
  - The xUnit v3 and TUnit runners on Microsoft.Testing.Platform don't support `--test-case-filter`; set `gate.commands.acceptanceMutation` for them.
- Neither has run against a real solution yet; read `logs/acceptance-coverage.log` and `logs/acceptance-mutation.log` after the first run.

**Not verified end-to-end:** the .NET path was written against current docs and tool source. It wasn't run against a real solution with NuGet packages while this plugin was built. The NPath and dependency-cycle checks were run against a sample solution on SDK 8.0.133 and 10.0.114, and the branch counting against coverlet-format reports written by hand. The first time you run `/atdd:init` and the gate in a .NET repo, read `.atdd/quality/latest/logs/*.log` once to confirm every tool ran as expected, `tests.log` in particular.
