using System.Numerics;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

if (args.Length != 3 || args[0] is not ("npath" or "namespaces"))
{
    Console.Error.WriteLine("usage: dotnet-quality npath|namespaces <file listing one .cs path per line> <output .json>");
    return 2;
}

var files = File.ReadAllLines(args[1]).Where(line => line.Length > 0).ToList();
var parseOptions = new CSharpParseOptions(LanguageVersion.Preview);
var trees = files.Select(path => CSharpSyntaxTree.ParseText(File.ReadAllText(path), parseOptions, path)).ToList();
object report = args[0] == "npath" ? NPath.Measure(trees) : NamespaceDependencies.Collect(trees);
File.WriteAllText(args[2], JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
return 0;

record MemberPaths(string File, string Name, int Start, int End, string NPath);

record Dependency(string From, string To, string File, int Line, string Symbol);

record NamespaceGraph(IReadOnlyList<string> Namespaces, IReadOnlyList<Dependency> Dependencies);

// NPath (Nejmeh, 1988) with the table PMD and Checkstyle document: statements in sequence multiply,
// decisions add their alternatives. C(expression) counts &&, ||, ??, ??= and `and`/`or` patterns once,
// each ?: twice and each switch-expression arm once. Lambdas and local functions are measured on their own.
static class NPath
{
    public static List<MemberPaths> Measure(IEnumerable<SyntaxTree> trees) =>
        trees.SelectMany(tree => tree.GetRoot().DescendantNodes()
                .Select(node => (node, body: BodyOf(node)))
                .Where(member => member.body is not null)
                .Select(member => Describe(tree.FilePath, member.node, Of(member.body!))))
            .Concat(trees.Select(TopLevelStatements).OfType<MemberPaths>())
            .ToList();

    static SyntaxNode? BodyOf(SyntaxNode node) => node switch
    {
        BaseMethodDeclarationSyntax method => (SyntaxNode?)method.Body ?? method.ExpressionBody,
        AccessorDeclarationSyntax accessor => (SyntaxNode?)accessor.Body ?? accessor.ExpressionBody,
        BasePropertyDeclarationSyntax property when property.AccessorList is null => ExpressionBodyOf(property),
        LocalFunctionStatementSyntax local => (SyntaxNode?)local.Body ?? local.ExpressionBody,
        AnonymousFunctionExpressionSyntax lambda => lambda.Body,
        _ => null,
    };

    static SyntaxNode? ExpressionBodyOf(BasePropertyDeclarationSyntax property) => property switch
    {
        PropertyDeclarationSyntax p => p.ExpressionBody,
        IndexerDeclarationSyntax i => i.ExpressionBody,
        _ => null,
    };

    static MemberPaths Describe(string file, SyntaxNode node, BigInteger npath)
    {
        var lines = node.GetLocation().GetLineSpan();
        return new MemberPaths(file, NameOf(node), lines.StartLinePosition.Line + 1, lines.EndLinePosition.Line + 1, npath.ToString());
    }

    static MemberPaths? TopLevelStatements(SyntaxTree tree)
    {
        var statements = tree.GetCompilationUnitRoot().Members.OfType<GlobalStatementSyntax>().ToList();
        if (statements.Count == 0) return null;
        var first = statements[0].GetLocation().GetLineSpan().StartLinePosition.Line + 1;
        var last = statements[^1].GetLocation().GetLineSpan().EndLinePosition.Line + 1;
        return new MemberPaths(tree.FilePath, "<top-level statements>", first, last, Sequence(statements.Select(s => s.Statement)).ToString());
    }

    static string NameOf(SyntaxNode node)
    {
        var own = node switch
        {
            MethodDeclarationSyntax m => m.Identifier.Text,
            ConstructorDeclarationSyntax c => c.Identifier.Text,
            DestructorDeclarationSyntax d => "~" + d.Identifier.Text,
            OperatorDeclarationSyntax o => "operator " + o.OperatorToken.Text,
            ConversionOperatorDeclarationSyntax c => "operator " + c.Type,
            AccessorDeclarationSyntax a => a.Keyword.Text,
            PropertyDeclarationSyntax p => p.Identifier.Text,
            IndexerDeclarationSyntax => "this[]",
            LocalFunctionStatementSyntax l => l.Identifier.Text,
            _ => $"lambda@{node.GetLocation().GetLineSpan().StartLinePosition.Line + 1}",
        };
        var outer = node.Ancestors().Select(OwnerName).OfType<string>().Reverse();
        return string.Join(".", outer.Append(own));
    }

    static string? OwnerName(SyntaxNode node) => node switch
    {
        BaseTypeDeclarationSyntax type => type.Identifier.Text,
        MethodDeclarationSyntax method => method.Identifier.Text,
        PropertyDeclarationSyntax property => property.Identifier.Text,
        IndexerDeclarationSyntax => "this[]",
        LocalFunctionStatementSyntax local => local.Identifier.Text,
        _ => null,
    };

    static BigInteger Of(SyntaxNode body) => body switch
    {
        BlockSyntax block => Sequence(block.Statements),
        ArrowExpressionClauseSyntax arrow => AtLeastOne(Conditions(arrow.Expression)),
        _ => AtLeastOne(Conditions(body)),
    };

    static BigInteger Sequence(IEnumerable<StatementSyntax> statements) =>
        statements.Aggregate(BigInteger.One, (product, statement) => product * Paths(statement));

    static BigInteger Paths(StatementSyntax statement) => statement switch
    {
        BlockSyntax block => Sequence(block.Statements),
        IfStatementSyntax node => Paths(node.Statement) + (node.Else is null ? 1 : Paths(node.Else.Statement)) + Conditions(node.Condition),
        SwitchStatementSyntax node => Switch(node),
        ForStatementSyntax node => Paths(node.Statement) + Conditions(node.Declaration) + Sum(node.Initializers)
            + Conditions(node.Condition) + Sum(node.Incrementors) + 1,
        CommonForEachStatementSyntax node => Paths(node.Statement) + Conditions(node.Expression) + 1,
        WhileStatementSyntax node => Paths(node.Statement) + Conditions(node.Condition) + 1,
        DoStatementSyntax node => Paths(node.Statement) + Conditions(node.Condition) + 1,
        TryStatementSyntax node => Try(node),
        UsingStatementSyntax node => AtLeastOne(Conditions(node.Declaration) + Conditions(node.Expression)) * Paths(node.Statement),
        LockStatementSyntax node => Paths(node.Statement),
        FixedStatementSyntax node => Paths(node.Statement),
        CheckedStatementSyntax node => Paths(node.Block),
        UnsafeStatementSyntax node => Paths(node.Block),
        LabeledStatementSyntax node => Paths(node.Statement),
        LocalFunctionStatementSyntax => 1,
        _ => AtLeastOne(Conditions(statement)),
    };

    static BigInteger Switch(SwitchStatementSyntax node)
    {
        var hasDefault = node.Sections.SelectMany(section => section.Labels).Any(label => label is DefaultSwitchLabelSyntax);
        var total = Conditions(node.Expression) + (hasDefault ? 0 : 1);
        foreach (var section in node.Sections)
        {
            var guards = section.Labels.OfType<CasePatternSwitchLabelSyntax>().Select(label => label.WhenClause?.Condition);
            total += Sequence(section.Statements) + section.Labels.Count - 1 + Sum(guards);
        }
        return total;
    }

    static BigInteger Try(TryStatementSyntax node)
    {
        var alternatives = node.Catches.Aggregate(Paths(node.Block),
            (sum, handler) => sum + Paths(handler.Block) + Conditions(handler.Filter?.FilterExpression));
        return alternatives * (node.Finally is null ? 1 : Paths(node.Finally.Block));
    }

    static BigInteger Conditions(SyntaxNode? node)
    {
        if (node is null or AnonymousFunctionExpressionSyntax or LocalFunctionStatementSyntax) return 0;
        BigInteger own = node switch
        {
            BinaryExpressionSyntax binary when binary.Kind() is SyntaxKind.LogicalAndExpression
                or SyntaxKind.LogicalOrExpression or SyntaxKind.CoalesceExpression => 1,
            AssignmentExpressionSyntax assignment when assignment.IsKind(SyntaxKind.CoalesceAssignmentExpression) => 1,
            BinaryPatternSyntax => 1,
            ConditionalExpressionSyntax => 2,
            SwitchExpressionSyntax switchExpression => switchExpression.Arms.Count,
            _ => 0,
        };
        return own + Sum(node.ChildNodes());
    }

    static BigInteger Sum(IEnumerable<SyntaxNode?> nodes) =>
        nodes.Aggregate(BigInteger.Zero, (sum, node) => sum + Conditions(node));

    static BigInteger AtLeastOne(BigInteger count) => BigInteger.Max(count, BigInteger.One);
}

// Namespace A depends on namespace B when code declared in A uses a type, or a member of a type,
// that this code base declares in B. A single compilation over all files binds the code base's own
// types; types from packages stay unresolved, which is fine because only our namespaces matter.
static class NamespaceDependencies
{
    const string Global = "(global namespace)";

    public static NamespaceGraph Collect(IReadOnlyList<SyntaxTree> trees)
    {
        var compilation = CSharpCompilation.Create("atdd-namespaces", trees, PlatformReferences(),
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
        var dependencies = new Dictionary<(string, string), Dependency>();
        foreach (var tree in trees)
        {
            var model = compilation.GetSemanticModel(tree);
            foreach (var name in tree.GetRoot().DescendantNodes().OfType<SimpleNameSyntax>())
            {
                var dependency = DependencyOf(name, model);
                if (dependency is not null) dependencies.TryAdd((dependency.From, dependency.To), dependency);
            }
        }
        var namespaces = trees.SelectMany(tree => tree.GetRoot().DescendantNodes().OfType<BaseTypeDeclarationSyntax>())
            .Select(NamespaceAround).Distinct().Order().ToList();
        return new NamespaceGraph(namespaces, dependencies.Values.OrderBy(d => d.From).ThenBy(d => d.To).ToList());
    }

    static IEnumerable<MetadataReference> PlatformReferences() =>
        ((string?)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") ?? "")
            .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries)
            .Select(path => MetadataReference.CreateFromFile(path));

    static Dependency? DependencyOf(SimpleNameSyntax name, SemanticModel model)
    {
        if (name.Ancestors().OfType<UsingDirectiveSyntax>().Any()) return null;
        var info = model.GetSymbolInfo(name);
        var symbol = info.Symbol ?? info.CandidateSymbols.FirstOrDefault();
        var type = symbol as INamedTypeSymbol ?? symbol?.ContainingType;
        if (type is null || !type.Locations.Any(location => location.IsInSource)) return null;
        var from = NamespaceAround(name);
        var to = NameOf(type.ContainingNamespace);
        if (from == to) return null;
        var line = name.GetLocation().GetLineSpan().StartLinePosition.Line + 1;
        return new Dependency(from, to, name.SyntaxTree.FilePath, line, type.ToDisplayString());
    }

    static string NamespaceAround(SyntaxNode node)
    {
        var names = node.Ancestors().OfType<BaseNamespaceDeclarationSyntax>().Reverse().Select(ns => ns.Name.ToString()).ToList();
        return names.Count == 0 ? Global : string.Join(".", names);
    }

    static string NameOf(INamespaceSymbol ns) => ns.IsGlobalNamespace ? Global : ns.ToDisplayString();
}
