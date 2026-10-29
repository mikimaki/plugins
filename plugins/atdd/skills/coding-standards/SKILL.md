---
name: coding-standards
description: Non-negotiable clean-code standards (plus C#-specific rules) that the atdd implementer writes to and the atdd reviewer judges against. Use whenever writing, reviewing or refactoring code in the ATDD pipeline. It enforces lean, intentional, readable code with no narration comments, no defensive bloat and no speculative abstraction.
---

# Clean Code

We are code artisans. Clients pay us precisely because our code is working, thoughtful, crystal clear, readable, and intentional — every line earns its place. These principles are the craft itself, not a style preference: apply them to all code, every time, without exception. Where principles could collide (validation, comments, duplication), the relevant section says which one wins.

Principles are ordered by impact.

## Bloat

AI-generated code runs far longer than what a careful human writes for the same functionality. Resist this actively: the best code is the code you didn't write. The five anti-patterns below are where slop concentrates — recognize them and avoid them.

### Narrating comments

Every comment restates the line below it. Zero information added. (The Comments section covers the rare comment that deserves to exist.)

**Instead of:**

```python
def total_area(rectangles):
    """Calculate the total area of all rectangles.

    Args:
        rectangles: A list of rectangles to calculate the total area for.

    Returns:
        The total area as a float.
    """
    # Initialize the total to zero
    total = 0
    # Loop through each rectangle in the list
    for rect in rectangles:
        # Multiply width by height and add to total
        total += rect.width * rect.height
    # Return the calculated total
    return total
```

**Do:**

```python
def total_area(rectangles):
    return sum(r.width * r.height for r in rectangles)
```

### A comment doing a name's job
 
The information is real — that is what makes these seem okay. But most of it is compensating for a weak name or an unextracted idea. Refactor until the names carry it; keep only the *why* that no name can express (see Comments).
 
**Instead of:**
 
```csharp
// Thirty samples — the usual rule of thumb for a sample mean to be roughly
// normal. Below this the confidence interval would be noise.
private const int MinSamples = 30;
```
 
**Do:**
 
```csharp
// Rule of thumb for a roughly normal sample mean; below this the interval is noise.
private const int MinSamplesForConfidenceInterval = 30;
```
 
The name absorbed the comment's *what*; the irreducible *why* — the rationale for this exact value — survives as one line. Deleting it would force the next maintainer to re-derive it before daring to change the value.
 
**Instead of:**
 
```csharp
// A zero Total means every value was zero, which would
// divide-by-zero the percentages below.
summary.HasPercentages = stats.Count >= MinSamples && stats.Total > 0;
if (!summary.HasPercentages)
{
    summary.Note = CountsOnlyNote(summary);
    return summary;
}
```
 
**Do:**
 
```csharp
summary.HasPercentages = CanComputePercentages(stats);
if (!summary.HasPercentages)
{
    summary.Note = CountsOnlyNote(summary);
    return summary;
}
```
 
```csharp
private static bool CanComputePercentages(Stats stats) =>
    stats.Count >= MinSamples && stats.Total > 0;
```
 
The predicate's name now says what the comment said, everywhere it's called.

### Defensive validation nobody asked for

Checks that restate what the runtime already enforces. The runtime's own error is just as loud and one line shorter. (See Structure for where validation does belong: boundaries.)

**Instead of:**

```python
def get_middle_element(xs):
    if xs is None:
        raise ValueError("Input list cannot be None")
    if not isinstance(xs, list):
        raise TypeError("Input must be a list")
    if len(xs) == 0:
        raise ValueError("Input list cannot be empty")
    middle_index = len(xs) // 2
    return xs[middle_index]
```

**Do:**

```python
def get_middle_element(xs):
    return xs[len(xs) // 2]
```

An empty list already raises a clear `IndexError` here; the hand-rolled checks merely restate it.

### Blanket try/except that makes things worse

Catching everything, printing, and limping on with `None` turns a loud, debuggable crash into silent corrupt state downstream. Let it propagate. (The Errors section covers handling done right.)

**Instead of:**

```python
try:
    with open(path, 'r') as f:
        content = f.read()
    config = json.loads(content)
except FileNotFoundError:
    print(f"Error: file {path} not found")
    config = None
except json.JSONDecodeError as e:
    print(f"Error: invalid JSON: {e}")
    config = None
except Exception as e:
    print(f"An unexpected error occurred: {e}")
    config = None
```

**Do:**

```python
config = json.loads(path.read_text())
```

### A class where a function would do

Don't build a class, with state and ceremony, around helper logic used in one place.

**Instead of:**

```python
class SlugGenerator:
    """A utility class for generating URL-friendly slugs."""

    def __init__(self, separator: str = "-"):
        self.separator = separator

    def generate(self, text: str) -> str:
        normalized = text.lower()
        cleaned = re.sub(r'[^a-z0-9]+', self.separator, normalized)
        return cleaned.strip(self.separator)

slug_generator = SlugGenerator()
slug = slug_generator.generate(title)
```

**Do:**

```python
def slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

slug = slugify(title)
```

### Intermediate variables as a substitute for thought

**Instead of:**

```javascript
// First, filter the users to get only active ones
const activeUsers = users.filter(user => user.active === true);

// Next, filter the active users to get only admins
const activeAdminUsers = activeUsers.filter(user => user.role === 'admin');

// Store the final result
const result = activeAdminUsers;
```

**Do:**

```javascript
const activeAdmins = users.filter(u => u.active && u.role === 'admin');
```

## Naming

Names carry most of a codebase's meaning. A reader should understand what something is without opening it.

- Name things for what they mean, not what they are: `retryDelayMs`, not `num2`.
- Functions are verbs (`loadConfig`), booleans read as predicates (`isExpired`, `hasAccess`).
- If a name needs a comment to explain it, the name is wrong. Rename instead of annotating.
- Match scope to length: `i` is fine in a 3-line loop; anything exported deserves a full name.
- Use each language's naming idiom — the same helper is `FilterStaleEvents` in C#, `filter_stale_events` in Python, `filterStaleEvents` in TypeScript. Translate conventions between languages; never transplant them.

## Functions

- One function, one job. If describing it requires "and", split it.
- Keep functions short enough to read without scrolling. Depth of nesting matters more than line count — prefer early returns over `else` pyramids.
- Fewer parameters beat more. Three or more related parameters usually want to be a struct/object.
- Avoid boolean flag parameters (`render(true)`); split into two named functions.
- No hidden side effects. A function named `getUser` must not also write to a cache, log, mutate state, or send email.
- Extract complex logic to a helper with an explanatory name: 50 lines of stale-event filtering inline becomes a call to `FilterStaleEvents` (or `filter_stale_events` — see Naming).

## Comments

Default: zero comments. Write code so clear it needs none — that is the craft. The only comment that survives review explains *why*: a decision, constraint, or trap invisible in the code itself.

- Before writing a comment, extract the code into a helper whose name says what the comment would have said (see Functions). That usually removes the need.
- Comment *why*, never *what*. The code already says what it does.
- Public API docstrings follow the project's documentation policy — but a docstring that merely restates the signature is narration. Delete it, or make it say what the signature can't: units, invariants, failure modes.
- Delete commented-out code. Version control remembers it.
- A comment compensating for confusing code is a signal to rewrite the code.
- A comment describes the code as it stands — never the conversation, task, or diff that
  produced it. "Previously", "now", "no longer", "also adds", "as requested" are history
  bleeding through: state the fact, not the transition. Facts about the outside world
  (a spec revision, a library bug) are fine; facts about this codebase's past are not.
  Test: the comment must be just as true and just as clear in the first commit as in
  the hundredth.

## Structure

- Match the existing codebase's conventions. House style beats personal style — don't restyle code you aren't otherwise changing.
- Don't repeat yourself — but only extract duplication that shares a *reason to change*. Two things that look alike but evolve independently should stay separate.
- Rule of three: duplication seen twice isn't worth extracting yet. The third occurrence proves the pattern — refactor then.
- Keep related things close: declare variables next to first use, keep helpers near their only caller.
- Depend on the narrowest interface that works. Accept an iterable, not a list; a reader, not a file.
- Validate at system boundaries — API handlers, file/network reads, user input — and raise on impossible states. Inside the boundary, trust your own code and let the runtime's natural error surface (see the validation anti-pattern above). This is the line between failing fast and defensive bloat.

## Errors

- Handle errors where you can act on them; let everything else propagate.
- Error messages say what failed, with what input, and what the caller can do about it.
- No bare `except:` / `catch (e) {}`. Catch the narrowest type you can handle.
- Prefer centralized exception handling (e.g., ASP.NET Core middleware) over scattered try/catch. Handle locally only where the code can recover or add context the central handler can't.

## Simplicity

- Write the obvious version first. Optimize only with a measurement in hand.
- YAGNI: don't build abstractions for hypothetical future needs. Three concrete uses justify an abstraction; one does not.
- Prefer boring, standard idioms of the language over clever ones. Cleverness is a cost the next reader pays.
- Deleting code is the best refactor. Less code, fewer bugs.

## Tests

- Test behavior through public interfaces, not implementation details. A refactor that changes no behavior should break no tests.
- One assertion of intent per test. Multiple asserts are fine when they verify a single behavior.
- Test names state method, condition, and expected outcome, in the language's own idiom: `CalculateTotal_EmptyList_ReturnsZero` in C# (Microsoft convention), `test_calculate_total_empty_list_returns_zero` in Python.
- If code is hard to test, that's a design smell — fix the coupling, don't mock harder.

## When reviewing code

Point at the specific line, state the principle it violates, and show the fixed version. Skip style nitpicks a formatter would catch. Distinguish blockers (bugs, hidden side effects, swallowed errors) from suggestions (naming, structure).

## C#

These apply on top of everything above when the code is C#.

- Depend on the narrowest interface that works, but, make sure you do not increase the number of iterations by switching List to IEnumerable, and forcing methods to call .ToList on the IEnumerable.
