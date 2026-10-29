# Binding Gherkin in .NET with Reqnroll

Reqnroll is the maintained successor of SpecFlow, with the same Gherkin and binding model.

## Packages (acceptance test project)

```xml
<ItemGroup>
  <PackageReference Include="Reqnroll.xUnit" Version="3.*" />            <!-- or Reqnroll.xunit.v3 / Reqnroll.NUnit / Reqnroll.MSTest: match the repo -->
  <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.*" />
  <PackageReference Include="xunit.runner.visualstudio" Version="3.*" />
  <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="<same major as ASP.NET Core>" />
  <PackageReference Include="Microsoft.Extensions.TimeProvider.Testing" Version="<same major as runtime>" />
  <!-- only when needed: Testcontainers.PostgreSql / .MsSql …, WireMock.Net -->
</ItemGroup>
```

Pin exact versions in the repo; the wildcards above only mark the family. Use whichever test framework the repo already uses.

## Strictness: `reqnroll.json` (in the acceptance test project root)

```json
{
  "$schema": "https://schemas.reqnroll.net/reqnroll-config-latest.json",
  "runtime": { "missingOrPendingStepsOutcome": "Error", "obsoleteBehavior": "Error" }
}
```

- With `Error`, an undefined or pending step fails the test. The default, `Pending`, lets a run look green.
- This file is a locked path: the implementer can't relax it.

## Layout

```
tests/<App>.Acceptance/
├── <App>.Acceptance.csproj
├── reqnroll.json
├── Features/<issue-key>/requirements.md, *.feature   # featuresDir = tests/<App>.Acceptance/Features
├── Steps/<Capability>Steps.cs
├── Drivers/<App>Driver.cs                             # thin API client over the in-process host
└── Support/Hooks.cs                                   # host, containers, clock, per-scenario reset
```

- Reqnroll generates `X.feature.cs` next to each feature, or under `obj/` when `ReqnrollUseIntermediateOutputPathForCodeBehind=true`. These files are generated code: exclude `*.feature.cs` from analyzers, lizard, jscpd and coverage.
- Gherkin tags become categories or traits (`[Trait("Category", tag)]` on xUnit), so `dotnet test --filter "Category=issue-proj-45"` runs one issue.

## In-process host and clock

```csharp
public sealed class AppFactory : WebApplicationFactory<Program>
{
    public FakeTimeProvider Clock { get; } = new(new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero));

    protected override void ConfigureWebHost(IWebHostBuilder builder) =>
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<TimeProvider>();
            services.AddSingleton<TimeProvider>(Clock);
            // replace only external boundaries here (third-party HTTP clients, email senders)
        });
}
```

For minimal APIs, `Program` must be visible to the test project. Add `public partial class Program;` or `InternalsVisibleTo`, whichever the repo already uses.

## Step definitions

```csharp
[Binding]
public sealed class ProfileSteps(AppDriver app, ScenarioContext scenario)
{
    [Given("a user with the display name {string}")]
    public async Task GivenUser(string displayName) =>
        scenario.Set(await app.CreateUserAsync(displayName));

    [When("the user changes their display name to {string}")]
    public async Task WhenDisplayNameChanged(string displayName) =>
        scenario.Set(await app.ChangeDisplayNameAsync(scenario.Get<User>().Id, displayName));

    [Then("the user is told {string}")]
    public void ThenTold(string message) =>
        Assert.Equal(message, scenario.Get<ChangeResult>().Message);

    [Then("the display name is still {string}")]
    public async Task ThenDisplayNameKept(string displayName)
    {
        var user = await app.GetUserAsync(scenario.Get<User>().Id);
        Assert.Equal(displayName, user.DisplayName);
    }
}
```

- Constructor (context) injection gives each scenario fresh instances, so there's no static state.
- Reqnroll supports Cucumber Expressions (`{string}`, `{int}`, `{decimal}`) as well as regex.
- Keep one step definition per phrasing.
- `[BeforeTestRun]` starts expensive shared infrastructure (Testcontainers). `[BeforeScenario]` resets data and the clock.

## Commands (typical values for `.claude/atdd.json`)

```bash
dotnet test tests/<App>.Acceptance                     # commands.acceptanceTests
dotnet test --filter "FullyQualifiedName!~Acceptance"  # commands.unitTests (adapt to the repo)
```

Coverage on .NET 10's Microsoft.Testing.Platform runner needs `coverlet.MTP` or `Microsoft.Testing.Extensions.CodeCoverage`; `coverlet.collector` only works with VSTest. See the `quality-gate` skill.
