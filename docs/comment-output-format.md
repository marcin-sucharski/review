# Comment Output Format

## Purpose

The output format is designed for coding agents. It must be concise, structured, and directly actionable.

The exact formatting can evolve, but stdout delivery and tmux delivery must use the same generated message.

The same generated message must also be written to the local review archive for every non-empty review.

The CLI defaults to Markdown output. Users may choose the format explicitly:

```text
review --output-format md
review --output-format xml
review -o xml
```

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

## XML Message Shape

Non-empty review output uses XML tags so coding agents can reliably identify repository metadata, files, comment locations, compact code context, and comment messages.

The XML structure follows these rules:

- use descriptive and consistent tag names,
- use nested tags for hierarchy,
- use attributes for stable metadata such as paths and line numbers,
- keep context compact as copied text inside CDATA,
- XML-escape message text so comments containing `<`, `>`, `&`, quotes, Markdown fences, or shell metacharacters remain literal,
- split embedded `]]>` in context CDATA and replace invalid XML control characters with `�` so the generated message remains parseable.

```xml
<review_feedback>
  <instructions>Use these review comments as feedback on the referenced code changes. For each review_comment, inspect the context and address the message.</instructions>
  <metadata>
    <repository path="/repo" />
    <source kind="branch" target_branch="main">branch comparison against main</source>
  </metadata>
  <review_comments>
    <file path="src/example.ts" display_path="src/example.ts">
      <review_comment id="c1">
        <location>
          <line_range side="new" start="42" end="44">Lines: 42-44</line_range>
        </location>
        <context radius="2"><![CDATA[  40   function getUserName(id) {
  41   const user = findUser(id);
  42   return user.name;
  43   }
]]></context>
        <message>This can throw when `findUser` returns null. Please handle the missing-user case.</message>
      </review_comment>
    </file>
  </review_comments>
</review_feedback>
```

## Line Labels

For new-side lines:

```xml
<line_range side="new" start="42" end="42">Line: 42</line_range>
<line_range side="new" start="42" end="44">Lines: 42-44</line_range>
```

For old-side deleted lines:

```xml
<line_range side="old" start="42" end="42">Old line: 42</line_range>
<line_range side="old" start="42" end="44">Old lines: 42-44</line_range>
```

For mixed ranges involving context and additions, use new-side line numbers when possible.

For mixed ranges involving deletions only, use old-side line numbers.

If a range cannot be represented cleanly as one side, include both old and new attributes:

```xml
<line_range side="mixed" old_start="42" old_end="43" new_start="42" new_end="44">Old lines: 42-43; New lines: 42-44</line_range>
```

## Context Lines

The selected code lines are included with nearby context. By default, the formatter includes two lines before and two lines after the commented range when those lines exist.

The context block is intentionally compact. It contains copied lines inside CDATA using the same line-oriented format as the previous plain text output:

```text
<line-number> <marker> <source text>
```

Suggested markers:

| Marker | Meaning |
| --- | --- |
| ` ` | context |
| `+` | added |
| `-` | deleted |

Example:

```xml
<context radius="2"><![CDATA[  42   function saveUser(user) {
  43 + return db.save(user);
  41 - return legacySave(user);
]]></context>
```

The formatter should preserve indentation in code text. If the copied source contains `]]>`, the formatter must split the CDATA sections so the XML remains parseable.

## Markdown Message Shape

Markdown output is the default and is also available explicitly with `--output-format md` or `-o md`. It is intended for agents or panes where plain Markdown is preferred.

Markdown output uses stable headings, file sections, line labels, fenced code context, and fenced comment bodies:

````text
Review comments for /repo
Source: branch comparison against main

File: src/example.ts

Lines: 42-44
```ts
  40   function getUserName(id) {
  41   const user = findUser(id);
  42   return user.name;
  43   }
```
Comment:
~~~text
This can throw when `findUser` returns null. Please handle the missing-user case.
~~~
````

The Markdown formatter must choose fence lengths that are longer than any run of backticks in code context or tildes in comment bodies.

## Multiple Comments In One File

Multiple comments in the same file should be grouped under one file heading and sorted by line number.

Example:

```xml
<file path="src/service.java" display_path="src/service.java">
  <review_comment id="c1">
    <location>
      <line_range side="new" start="30" end="32">Lines: 30-32</line_range>
    </location>
    <context radius="2"><![CDATA[  30   ...
]]></context>
    <message>...</message>
  </review_comment>
  <review_comment id="c2">
    <location>
      <line_range side="new" start="80" end="80">Line: 80</line_range>
    </location>
    <context radius="2"><![CDATA[  80   ...
]]></context>
    <message>...</message>
  </review_comment>
</file>
```

## Empty Comment Set

If no comments were added, stdout delivery should print nothing or a clear empty-review message depending on the final quit behavior.

Preferred behavior:

```text
No review comments.
```

Tmux delivery should not send an empty message unless the user explicitly confirms.

Empty reviews are not archived.

## Local Review Archive

Every non-empty review is saved as one JSON file before stdout or tmux delivery.

Archive directory:

```text
$XDG_DATA_HOME/review/reviews
```

Fallback when `XDG_DATA_HOME` is unset:

```text
~/.local/share/review/reviews
```

Required JSON shape:

```json
{
  "path": "/absolute/path/to/repository",
  "branch": "feature/review",
  "review_message": "Review comments for /absolute/path/to/repository\n..."
}
```

`review_message` must be byte-for-byte the same text sent to tmux or printed to stdout for that review.

For detached HEAD, `branch` may use a stable descriptive value such as `detached:<short-sha>`.

## Escaping And Safety

The formatter must handle:

- comments containing XML metacharacters,
- comments containing Markdown fences,
- comments containing shell-sensitive characters,
- code containing XML metacharacters,
- code containing tabs,
- Unicode source text,
- very long lines.

Tmux delivery must send text as literal content, not execute it as shell syntax.

## Example Full XML Output

```xml
<review_feedback>
  <instructions>Use these review comments as feedback on the referenced code changes. For each review_comment, inspect the context and address the message.</instructions>
  <metadata>
    <repository path="/home/suchar/projects/example" />
    <source kind="branch" target_branch="main">branch comparison against main</source>
  </metadata>
  <review_comments>
    <file path="src/UserService.ts" display_path="src/UserService.ts">
      <review_comment id="c1">
        <location>
          <line_range side="new" start="42" end="44">Lines: 42-44</line_range>
        </location>
        <context radius="2"><![CDATA[  42   const user = await repo.find(id);
  43   return user.name;
  44
]]></context>
        <message>This should handle the missing-user path. Please return a typed error or throw the same domain exception used by the rest of this service.</message>
      </review_comment>
    </file>
    <file path="tests/UserService.test.ts" display_path="tests/UserService.test.ts">
      <review_comment id="c2">
        <location>
          <line_range side="new" start="19" end="19">Line: 19</line_range>
        </location>
        <context radius="2"><![CDATA[  19   expect(result.name).toEqual("Ada");
]]></context>
        <message>Please add coverage for an unknown user id so the behavior above is locked down.</message>
      </review_comment>
    </file>
  </review_comments>
</review_feedback>
```

## Determinism

The output should be deterministic for tests:

- stable file order,
- stable comment order,
- stable heading text,
- no timestamps unless explicitly requested,
- normalized newlines.

The archive filename may include a timestamp and random suffix. The timestamp belongs to the filename, not the deterministic review message.
