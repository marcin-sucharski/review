# Comment Output Format

## Purpose

The output format is designed for coding agents. It must be concise, structured, and directly actionable.

The exact formatting can evolve, but stdout delivery and tmux delivery must use the same generated message.

## Required Content

Each review message must include:

- repository path or repository name,
- review source,
- target branch when branch comparison is used,
- one section per file with comments,
- line range for each comment,
- selected context lines,
- comment body.

Files without comments should not appear in the final review message unless the user explicitly asks for a full summary in a future version.

## Recommended Message Shape

```text
Review comments for <repository>
Source: <uncommitted changes | branch comparison against TARGET>

File: src/example.ts
Lines: 42-44
```ts
42  const user = findUser(id);
43  return user.name;
44
```
Comment:
This can throw when `findUser` returns null. Please handle the missing-user case.

File: tests/example.test.ts
Line: 18
```ts
18  expect(result).toBe(true);
```
Comment:
Add a negative test for the null-user case.
```

Because Markdown code fences can conflict with code containing backticks, the formatter should choose a fence length that is longer than any backtick run in the selected context.

## Line Labels

For new-side lines:

```text
Line: 42
Lines: 42-44
```

For old-side deleted lines:

```text
Old line: 42
Old lines: 42-44
```

For mixed ranges involving context and additions, use new-side line numbers when possible.

For mixed ranges involving deletions only, use old-side line numbers.

If a range cannot be represented cleanly as one side, include both:

```text
Old lines: 42-43
New lines: 42-44
```

## Context Lines

The selected code lines are included exactly as reviewed.

Each context line should include:

- line number,
- change marker when useful,
- source text.

Suggested markers:

| Marker | Meaning |
| --- | --- |
| ` ` | context |
| `+` | added |
| `-` | deleted |

Example:

```text
 42  function saveUser(user) {
+43    return db.save(user);
-41    return legacySave(user);
```

The formatter should preserve indentation in code text.

## Multiple Comments In One File

Multiple comments in the same file should be grouped under one file heading and sorted by line number.

Example:

```text
File: src/service.java

Lines: 30-32
```java
30  ...
```
Comment:
...

Line: 80
```java
80  ...
```
Comment:
...
```

## Empty Comment Set

If no comments were added, stdout delivery should print nothing or a clear empty-review message depending on the final quit behavior.

Preferred behavior:

```text
No review comments.
```

Tmux delivery should not send an empty message unless the user explicitly confirms.

## Escaping And Safety

The formatter must handle:

- comments containing Markdown fences,
- comments containing shell-sensitive characters,
- code containing tabs,
- Unicode source text,
- very long lines.

Tmux delivery must send text as literal content, not execute it as shell syntax.

## Example Full Output

````text
Review comments for /home/suchar/projects/example
Source: branch comparison against main

File: src/UserService.ts

Lines: 42-44
```ts
42  const user = await repo.find(id);
43  return user.name;
44
```
Comment:
This should handle the missing-user path. Please return a typed error or throw the same domain exception used by the rest of this service.

File: tests/UserService.test.ts

Line: 19
```ts
19  expect(result.name).toEqual("Ada");
```
Comment:
Please add coverage for an unknown user id so the behavior above is locked down.
````

## Determinism

The output should be deterministic for tests:

- stable file order,
- stable comment order,
- stable heading text,
- no timestamps unless explicitly requested,
- normalized newlines.
