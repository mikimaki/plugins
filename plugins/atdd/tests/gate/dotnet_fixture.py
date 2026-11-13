"""A throwaway .NET repo for the quality gate: `main` holds a solution of three production projects (Steps.Goals,
Steps.Push, and Steps.Reports, which references Steps.Goals), one xUnit test project per production project, and a
Reqnroll acceptance test project referencing Steps.Push; `feature` adds GoalBook.ClearGoal with its unit test.
The NuGet packages must already be in the local cache, or reachable.

    repo = build(target)                  # target is created or replaced
    report = run_gate(repo, "fast")       # report.json as a dict; extra args go to gate.py
    add_to_base(repo, path, text)         # a file committed on main, under the feature branch
    add_to_feature(repo, path, text)      # a file committed on the feature branch
"""
import json
import shutil
import subprocess
from pathlib import Path

from fixture import add_to_base, add_to_feature, commit, git, run_gate, write  # noqa: F401  (re-exported for the tests)

GATE = Path(__file__).resolve().parents[2] / "skills" / "quality-gate" / "scripts" / "gate.py"

ATDD = {
    "stack": "dotnet",
    "baseBranch": "main",
    "featuresDir": "tests/Steps.Acceptance/Features",
    "gate": {"solution": "Steps.sln", "sourceDirs": ["src"], "testDirs": ["tests"]},
}

LIBRARY = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
{references}</Project>
"""

TEST_PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.14.1" />
    <PackageReference Include="coverlet.collector" Version="6.0.4" PrivateAssets="all" />
{packages}  </ItemGroup>
{references}</Project>
"""

XUNIT = """    <PackageReference Include="xunit" Version="2.9.3" />
    <PackageReference Include="xunit.runner.visualstudio" Version="3.1.4" PrivateAssets="all" />
"""

REQNROLL = """    <PackageReference Include="Reqnroll.xUnit" Version="3.3.4" />
    <PackageReference Include="xunit" Version="2.9.3" />
    <PackageReference Include="xunit.runner.visualstudio" Version="3.1.4" PrivateAssets="all" />
"""

GOAL_BOOK = """namespace Steps.Goals;

public class GoalBook
{
    private readonly Dictionary<string, int> _goals = new();

    public void SetGoal(string member, int steps) => _goals[member] = steps;

    public int? GoalOf(string member) => _goals.TryGetValue(member, out var steps) ? steps : null;
}
"""

CLEAR_GOAL = """
    public void ClearGoal(string member)
    {
        _goals.Remove(member);
    }
}
"""

PUSH_GATEWAY = """namespace Steps.Push;

public static class PushGateway
{
    public static string Url => "https://push.example.com";
}
"""

WEEKLY_REPORT = """using Steps.Goals;

namespace Steps.Reports;

public static class WeeklyReport
{
    public static string Of(GoalBook book, string member) => $"{member}: {book.GoalOf(member)}";
}
"""

GOAL_TESTS = """using Steps.Goals;
using Xunit;

namespace Steps.Goals.Tests;

public class GoalBookTests
{
    [Fact]
    public void Saved_goal_is_read_back()
    {
        var book = new GoalBook();
        book.SetGoal("robin", 8000);
        Assert.Equal(8000, book.GoalOf("robin"));
    }
}
"""

CLEAR_GOAL_TESTS = """using Steps.Goals;
using Xunit;

namespace Steps.Goals.Tests;

public class ClearGoalTests
{
    [Fact]
    public void Cleared_goal_is_gone()
    {
        var book = new GoalBook();
        book.SetGoal("robin", 8000);
        book.ClearGoal("robin");
        Assert.Null(book.GoalOf("robin"));
    }
}
"""

PUSH_TESTS = """using Steps.Push;
using Xunit;

namespace Steps.Push.Tests;

public class PushGatewayTests
{
    [Fact]
    public void Gateway_is_https() => Assert.StartsWith("https://", PushGateway.Url);
}
"""

REPORT_TESTS = """using Steps.Goals;
using Steps.Reports;
using Xunit;

namespace Steps.Reports.Tests;

public class WeeklyReportTests
{
    [Fact]
    public void Report_names_the_member() => Assert.StartsWith("robin", WeeklyReport.Of(new GoalBook(), "robin"));
}
"""

PUSH_FEATURE = """Feature: Push messages

  Scenario: The gateway is secure
    Given the push gateway
    Then the gateway uses https
"""

PUSH_STEPS = """using Reqnroll;
using Steps.Push;
using Xunit;

namespace Steps.Acceptance.StepDefinitions;

[Binding]
public class PushSteps
{
    private string _url = "";

    [Given("the push gateway")]
    public void GivenThePushGateway() => _url = PushGateway.Url;

    [Then("the gateway uses https")]
    public void ThenTheGatewayUsesHttps() => Assert.StartsWith("https://", _url);

    [Then("the gateway answers in time")]
    public void ThenTheGatewayAnswersInTime() => Assert.Equal("the gateway answered in time", _url);
}
"""

REQNROLL_JSON = """{
  "$schema": "https://schemas.reqnroll.net/reqnroll-config-latest.json",
  "runtime": { "missingOrPendingStepsOutcome": "Error" }
}
"""

GITIGNORE = "bin/\nobj/\n.atdd/\nStrykerOutput/\n"

PROJECTS = {
    "src/Steps.Goals/Steps.Goals.csproj": ("library", []),
    "src/Steps.Push/Steps.Push.csproj": ("library", []),
    "src/Steps.Reports/Steps.Reports.csproj": ("library", ["../Steps.Goals/Steps.Goals.csproj"]),
    "tests/Steps.Goals.Tests/Steps.Goals.Tests.csproj": ("xunit", ["../../src/Steps.Goals/Steps.Goals.csproj"]),
    "tests/Steps.Push.Tests/Steps.Push.Tests.csproj": ("xunit", ["../../src/Steps.Push/Steps.Push.csproj"]),
    "tests/Steps.Reports.Tests/Steps.Reports.Tests.csproj": ("xunit", [r"..\..\src\Steps.Reports\Steps.Reports.csproj"]),
    "tests/Steps.Acceptance/Steps.Acceptance.csproj": ("reqnroll", ["../../src/Steps.Push/Steps.Push.csproj"]),
}

FILES = {
    "src/Steps.Goals/GoalBook.cs": GOAL_BOOK,
    "src/Steps.Push/PushGateway.cs": PUSH_GATEWAY,
    "src/Steps.Reports/WeeklyReport.cs": WEEKLY_REPORT,
    "tests/Steps.Goals.Tests/GoalBookTests.cs": GOAL_TESTS,
    "tests/Steps.Push.Tests/PushGatewayTests.cs": PUSH_TESTS,
    "tests/Steps.Reports.Tests/WeeklyReportTests.cs": REPORT_TESTS,
    "tests/Steps.Acceptance/Features/Push.feature": PUSH_FEATURE,
    "tests/Steps.Acceptance/StepDefinitions/PushSteps.cs": PUSH_STEPS,
    "tests/Steps.Acceptance/reqnroll.json": REQNROLL_JSON,
    ".gitignore": GITIGNORE,
    ".claude/atdd.json": json.dumps(ATDD, indent=2) + "\n",
}

STRYKER_TOOL = {"version": 1, "isRoot": True,
                "tools": {"dotnet-stryker": {"version": "5.0.0", "commands": ["dotnet-stryker"], "rollForward": False}}}


def project_file(kind: str, references: list) -> str:
    refs = "".join(f'    <ProjectReference Include="{r}" />\n' for r in references)
    refs = f"  <ItemGroup>\n{refs}  </ItemGroup>\n" if refs else ""
    if kind == "library":
        return LIBRARY.format(references=refs)
    return TEST_PROJECT.format(packages=XUNIT if kind == "xunit" else REQNROLL, references=refs)


def dotnet(repo: Path, *args: str) -> None:
    subprocess.run(["dotnet", *args], cwd=repo, check=True, capture_output=True, text=True)


def build(target: Path, with_stryker: bool = False) -> Path:
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for path, (kind, references) in PROJECTS.items():
        write(target, path, project_file(kind, references))
    for path, text in FILES.items():
        write(target, path, text)
    if with_stryker:
        write(target, ".config/dotnet-tools.json", json.dumps(STRYKER_TOOL, indent=2) + "\n")
        dotnet(target, "tool", "restore")
    (target / "scripts" / "atdd").mkdir(parents=True)
    shutil.copy(GATE, target / "scripts" / "atdd" / "gate.py")
    dotnet(target, "new", "sln", "-n", "Steps", "--format", "sln")
    dotnet(target, "sln", "Steps.sln", "add", *PROJECTS)
    git(target, "init", "-q", "-b", "main")
    git(target, "config", "user.email", "fixture@example.com")
    git(target, "config", "user.name", "fixture")
    commit(target, "Steps with its push scenario")
    git(target, "switch", "-q", "-c", "feature")
    goal_book = (target / "src/Steps.Goals/GoalBook.cs").read_text(encoding="utf-8")
    write(target, "src/Steps.Goals/GoalBook.cs", goal_book[: goal_book.rstrip().rindex("}")] + CLEAR_GOAL.lstrip("\n"))
    write(target, "tests/Steps.Goals.Tests/ClearGoalTests.cs", CLEAR_GOAL_TESTS)
    commit(target, "Members can clear their goal")
    return target
