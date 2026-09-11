#!/usr/bin/env python3
"""Run required live regression in an isolated tmux server.

Run from any directory after building target/release/review:
    python tests/live_tmux_regression.py

Requires Python 3, Git, Bash, and tmux. Captures and fixture repositories remain
in the printed temporary directory for inspection. Only this test's tmux server
is stopped; the user's tmux sessions are untouched.
"""
import json
import os
import pathlib
import re
import shlex
import subprocess as sp
import tempfile
import time

binary = str(pathlib.Path(__file__).resolve().parents[1] / 'target/release/review')
root = pathlib.Path(tempfile.mkdtemp(prefix='review-live-'))
repo = root / 'repo'
repo.mkdir()
sock = 'review_regression_' + str(os.getpid())

def run(*args, cwd=repo):
    result = sp.run(args, cwd=cwd, text=True, stdout=sp.PIPE, stderr=sp.PIPE)
    if result.returncode:
        raise RuntimeError(f'{args!r}: {result.stderr}')
    return result.stdout

def git(*args):
    return run('git', *args)

def write(name, text):
    (repo / name).write_text(text)

def tm(*args):
    return run('tmux', '-L', sock, '-f', '/dev/null', *args)

def keys(*args):
    tm('send-keys', '-t', 'run:0.0', *args)
    time.sleep(0.13)

def literal(text):
    keys('-l', text)

def cap(name=None, ansi=False):
    s = tm('capture-pane', '-p', *(['-e'] if ansi else []), '-t', 'run:0.0')
    if name:
        (root / (name + '.txt')).write_text(s)
    return s

def check(ok, label):
    if not ok:
        cap('FAIL')
        raise AssertionError(label + '; artifacts ' + str(root))
    print('PASS ' + label, flush=True)

def wait_for(predicate, label):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate(cap()):
            check(True, label)
            return
        time.sleep(0.1)
    check(False, label)


def start(args='', directory=repo):
    command = (
        "unset NO_COLOR; "
        f"export XDG_DATA_HOME={shlex.quote(str(root / 'data'))}; "
        f"{shlex.quote(binary)} {args}; "
        "echo REVIEW_EXIT:$?; exec bash --noprofile --norc"
    )
    tm('respawn-pane', '-k', '-t', 'run:0.0', '-c', str(directory),
       'bash -c ' + shlex.quote(command))
    time.sleep(0.45)

def quit():
    literal(':q')
    keys('Enter')

def comment(text):
    keys('Enter')
    literal(text)
    keys('Enter')
git('init', '-q', '-b', 'main')
git('config', 'user.email', 'test@example.invalid')
git('config', 'user.name', 'Regression')
fixtures = {'a.py': 'value = 42',
 'b.md': '# Heading with **bold**',
 'c.java': 'public class Main {}',
 'd.js': 'const x = 42;',
 'e.ts': 'const x: number = 42;',
 'f.css': 'body { color: red; }',
 'g.html': '<h1>Hi</h1>',
 'h.jsx': 'const x = <h1>Hi</h1>;',
 'i.sql': 'SELECT id FROM users;',
 'j.xml': '<user id="1"/>',
 'k.json': '{"value":42}',
 'l.properties': '# Application properties',
 'm.yaml': 'value: true',
 'n.nix': '{ description = "example"; }',
 'o.lock': '{"version":42}',
 '.gitignore': '*.cache',
 'main.tf': 'resource "null_resource" "test" { count = 42 }',
 'prod.tfvars': 'instance_count = 43',
 '.terraform.lock.hcl': 'provider "example/test" { version = "1" }'}
for name, source in fixtures.items():
    write(name, source + '\n')
write('long.txt', ''.join((f'line {i:03d}\n' for i in range(250))))
write('deleted.txt', 'delete me\n')
write('old.txt', 'rename me\n' * 10)
git('add', '.')
git('commit', '-qm', 'baseline')
git('checkout', '-qb', 'feature')
write('committed.txt', 'committed branch change\n')
git('add', '.')
git('commit', '-qm', 'feature commit')
git('branch', 'master', 'main')
git('branch', 'stack/first')
git('checkout', '-qb', 'stack/second')
write('second-layer.tf', 'instance_count = 99\n')
git('add', '.')
git('commit', '-qm', 'second stacked layer')
git('checkout', '-q', 'feature')

for name, source in fixtures.items():
    write(name, source + '\n' + source + '\n')
write('long.txt', ''.join((('CHANGED' if i in (100, 200) else 'line') + f' {i:03d}\n' for i in range(250))))
git('rm', 'deleted.txt')
git('mv', 'old.txt', 'renamed.txt')
write('staged.txt', 'staged change\n')
git('add', 'staged.txt')
write('unstaged.txt', 'untracked change\n')
try:
    tm('new-session', '-d', '-s', 'run', '-x', '280', '-y', '40', '-c', str(repo), 'bash --noprofile --norc')
    start()
    s = cap('menu')
    check(s.index('PR-style') < s.index('uncommitted'), 'menu PR before uncommitted')
    keys('Down')
    check(cap() != s, 'menu arrow navigation')
    keys('C-c')
    check('REVIEW_EXIT:130' in cap(), 'menu Ctrl+C')
    start('--source uncommitted --stdout')
    check('Files' not in cap().splitlines()[0], 'file pane hidden')
    keys('T')
    check('Files' in cap(), 'T file pane toggle')
    keys('Tab', 'Down', 'Enter')
    cap('tree')
    check('.terraform.lock.hcl' in cap().splitlines()[0].split('│', 1)[1], 'file tree selection')
    keys('Tab', 'Tab')
    cap('focus')
    start('--source uncommitted --stdout')
    comment('single comment')
    check('single comment' in cap(), 'single-line comment')
    keys('Enter')
    literal(' edited')
    keys('Enter')
    check('single comment edited' in cap(), 'immediate edit after save')
    keys('BSpace')
    check('single comment edited' not in cap(), 'delete comment')
    keys('Down', 'S-Down', 'Enter')
    literal('multi first')
    keys('C-j')
    literal('multi second')
    keys('Enter')
    check('multi first' in cap() and 'multi second' in cap(), 'multiline selection and Ctrl+J')
    keys('T', 'Tab', 'Tab')
    comment_header = next(line.split('│', 1)[0] for line in cap(ansi=True).splitlines() if ' Comments' in line)
    check(re.search(r'\x1b\[[0-9;]*7m', comment_header), 'comment list focus')
    quit()
    s = cap('stdout')
    check('multi first' in s and '```' in s, 'Markdown stdout')
    archives = list((root / 'data/review/reviews').glob('*.json'))
    check(bool(archives), 'archive creation')
    check('multi second' in json.loads(archives[-1].read_text())['review_message'], 'archive content')
    for args, choice, label in [('--source uncommitted', 1, 'stdout delivery prompt'), ('--source uncommitted', 0, 'save file delivery'), ('--source uncommitted -o xml --stdout', None, 'XML stdout')]:
        start(args)
        comment(label)
        quit()
        if choice is not None:
            check('Delivery target' in cap(), 'delivery menu ' + label)
            if choice:
                keys('Down')
            keys('Enter')
        s = cap(label.replace(' ', '_'))
        if choice == 0:
            check(any((label in p.read_text() for p in repo.glob('review-*.md'))), label)
        elif choice is None:
            check('<review' in s and label in s, label)
        else:
            check(label in s, label)
    start('--source branch --target main --stdout')
    keys('T')
    s = cap('branch')
    check('committed.txt' in s, 'branch committed file')
    for needle in ['committed branch change', 'staged change', 'untracked change', 'CHANGED 100']:
        literal('/' + needle)
        keys('Enter')
        check(needle in cap(), 'branch content ' + needle)
    for name, source in fixtures.items():
        start('--source uncommitted --stdout')
        keys('T')
        literal('/' + source)
        keys('Enter')
        literal('/')
        keys('Enter')
        s = cap('syntax_' + name.replace('/', '_'), True)
        source_rows = [line.split('│', 2)[2] for line in s.splitlines() if line.count('│') >= 2 and source in re.sub('\\x1b\\[[0-9;]*m', '', line)]
        check(source_rows and any((re.search('\\x1b\\[[0-9;]*(?:3[0-7]|38;5;[0-9]+)m', line) for line in source_rows)), 'live highlighted ' + name)
    literal('/delete me')
    keys('Enter')
    check('deleted.txt' in cap(), 'deleted file')
    literal('/rename me')
    keys('Enter')
    check('renamed.txt' in cap(), 'renamed file')
    literal('/CHANGED 100')
    keys('Enter')
    s = cap('expansion')
    check('Show ' in s, 'expansion rows')
    row = next((i + 1 for i, line in enumerate(s.splitlines()) if 'Show 20 lines below' in line))
    literal(f'\x1b[<0;80;{row}M')
    expanded = cap('expanded')
    check('line 121' in expanded and expanded != s, 'expansion activation')
    literal('/CHANGED 100')
    keys('Enter')
    a = cap()
    literal('\x1b[<65;60;10M')
    b = cap()
    check(a != b, 'mouse wheel normal column')
    literal('\x1b[<65;260;10M')
    c = cap('mouse_right')
    check(b != c, 'mouse wheel far-right column')
    check('long.txt' in c.splitlines()[0].split('│', 1)[1], 'sticky file header')
    top_before = c.splitlines()[1].split('│', 1)[1]
    literal('e')
    expanded = cap('whole_file')
    check('Expanded all context in long.txt.' in expanded, 'e expands current file')
    check(expanded.splitlines()[1].split('│', 1)[1] == top_before, 'e preserves viewport')
    for needle in ['line 000', 'line 150', 'line 249']:
        literal('/' + needle)
        keys('Enter')
        check(needle in cap() and 'No matches' not in cap(), 'whole file context ' + needle)
    literal('e')
    check('Expanded all context in long.txt.' in cap(), 'repeated e is safe')
    keys('C-c')
    check('Press Ctrl+C again' in cap(), 'first interrupt warning')
    keys('Down')
    check('Press Ctrl+C again' not in cap(), 'interrupt warning cleared')
    keys('C-c', 'C-c')
    check('REVIEW_EXIT:130' in cap(), 'double interrupt quit')
    start('--stdout')
    keys('Down', 'Down', 'Enter')
    s = cap('commits_menu')
    check('feature commit' in s and 'baseline' in s, 'recent commit picker')
    keys('Down', 'Enter')
    s = cap('root_commit')
    comment('root snapshot comment')
    quit()
    check(git('rev-parse', 'main').strip() in cap() and 'root snapshot comment' in cap(), 'specific root commit snapshot')
    start('--stdout')
    keys('Down', 'Down', 'Down', 'Enter')
    check('Number of last commits' in cap(), 'last N prompt')
    literal('2')
    keys('Enter')
    comment('range snapshot comment')
    quit()
    check('last 2 commits' in cap(), 'last N snapshot')
    start('--commit HEAD --stdout')
    check('committed branch change' in cap(), 'commit CLI')
    write('committed.txt', 'CHANGED WORKTREE\n')
    time.sleep(0.4)
    check('CHANGED WORKTREE' not in cap(), 'snapshot stays frozen')
    quit()
    start('--last 1 --stdout')
    check('committed branch change' in cap(), 'last CLI')
    quit()
    tm('new-window', '-d', '-t', 'run', '-n', 'receiver', '-c', str(repo), 'cat > ' + shlex.quote(str(root / 'received.txt')))
    start('--source branch --target main')
    comment('tmux delivered comment')
    quit()
    s = cap('delivery_tmux')
    keys('End', 'Enter')
    time.sleep(0.3)
    check('tmux delivered comment' in (root / 'received.txt').read_text(), 'tmux pane delivery')
    checkout_before = git('rev-parse', 'HEAD')
    status_before = git('status', '--porcelain=v1')
    start('--stdout')
    keys('End', 'Enter')
    source_menu = cap('stacked_source_menu')
    check('Branch to review' in source_menu and source_menu.index('master') < source_menu.index('main'), 'stacked source picker ordering')
    literal('stack/second')
    keys('Enter')
    target_menu = cap('stacked_target_menu')
    check('Target branch' in target_menu and 'stack/second -> master' in target_menu, 'stacked target picker ordering and source')
    literal('stack/first')
    keys('Enter')
    check('second-layer.tf' in cap() and 'instance_count = 99' in cap(), 'stacked second layer snapshot')
    comment('stacked layer comment')
    quit()
    message = cap('stacked_markdown')
    check('source branch stack/second' in message and 'target branch stack/first' in message, 'stacked Markdown branch names')
    check(git('rev-parse', 'stack/second').strip() in message and git('rev-parse', 'stack/first').strip() in message, 'stacked Markdown exact commits')
    reviews = [json.loads(path.read_text()) for path in (root / 'data/review/reviews').glob('*.json')]
    check(any(review['branch'] == 'stack/second' and 'stacked layer comment' in review['review_message'] for review in reviews), 'stacked archive uses source branch')
    check(git('rev-parse', 'HEAD') == checkout_before and git('status', '--porcelain=v1') == status_before, 'stacked preserves dirty checkout')
    start('--branch stack/first --target master --stdout')
    check('committed branch change' in cap(), 'stacked first layer against base')
    comment('first layer comment')
    quit()
    check('source branch stack/first' in cap() and 'target branch master' in cap(), 'stacked CLI output names')
    reload_repo = root / 'reload'
    reload_repo.mkdir()
    def reload_git(*args):
        return run('git', *args, cwd=reload_repo)
    reload_git('init', '-q', '-b', 'main')
    reload_git('config', 'user.email', 'test@example.invalid')
    reload_git('config', 'user.name', 'Reload Regression')
    (reload_repo / 'active.txt').write_text('base active\n')
    (reload_repo / 'tracked.txt').write_text('base tracked\n')
    reload_git('add', '.')
    reload_git('commit', '-qm', 'base')
    reload_git('checkout', '-qb', 'feature')
    (reload_repo / 'active.txt').write_text('modified active\n')
    start('--source branch --target main --stdout', directory=reload_repo)
    keys('T')
    (reload_repo / 'tracked.txt').write_text('modified tracked\n')
    wait_for(lambda text: 'tracked.txt' in text, 'reload discovers newly modified tracked file')
    (reload_repo / 'new.txt').write_text('new untracked content\n')
    wait_for(lambda text: 'new.txt' in text, 'reload discovers new untracked file')
    keys('Enter')
    literal('draft must not move')
    (reload_repo / 'active.txt').write_text('base active\n')
    wait_for(lambda text: 'active.txt' not in text, 'reload removes reverted uncommented PR file')
    check('draft must not move' not in cap(), 'reload cancels draft on removed file')
    literal('/modified tracked')
    keys('Enter')
    comment('retain this feedback')
    (reload_repo / 'tracked.txt').write_text('base tracked\n')
    wait_for(lambda text: 'No differences from the review base' in text and 'retain this feedback' in text, 'reload retains reverted file with comments')
    keys('BSpace')
    wait_for(lambda text: 'tracked.txt' not in text, 'deleting last comment removes unchanged file')
    (reload_repo / 'new.txt').unlink()
    wait_for(lambda text: 'No changes to review.' in text, 'reload can reach empty review')
    keys('Tab', 'Enter', 'Down')
    (reload_repo / 'fresh.txt').write_text('new after empty\n')
    wait_for(lambda text: 'fresh.txt' in text, 'reload discovers file after empty review')
    keys('Enter')
    check('Untracked file' in cap(), 'reload new file metadata')
    reload_git('add', 'fresh.txt')
    wait_for(lambda text: 'fresh.txt' in text and 'Untracked file' not in text, 'reload observes index-only staging')
    quit()
    start('--source uncommitted --stdout', directory=reload_repo)
    (reload_repo / 'fresh.txt').unlink()
    wait_for(lambda text: 'No changes to review.' in text, 'uncommitted reload removes vanished change')
    (reload_repo / 'later.txt').write_text('later change\n')
    wait_for(lambda text: 'later.txt' in text, 'uncommitted reload discovers later file')
    quit()
    for index in range(22):
        git('commit', '--allow-empty', '-qm', f'picker-{index:02d} ' + 'long subject ' * 12)
    tm('resize-window', '-t', 'run:0', '-x', '80', '-y', '16')
    start('--source commit --stdout')
    check('picker-21' in cap(), 'small commit picker shows newest selection')
    keys('End')
    check('picker-02' in cap(), 'small commit picker navigates to oldest of 20')
    keys('Home')
    check('picker-21' in cap(), 'small commit picker returns to newest')
    keys('Enter')
    check('no changes found in selected commits' in cap(), 'small commit picker selects snapshot')
    print('ARTIFACTS ' + str(root), flush=True)
finally:
    tm('kill-server')
